# -*- coding: utf-8 -*-
import requests
from requests.auth import HTTPBasicAuth
import json
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    x_duoplane_shipment_id = fields.Integer(string='Duoplane Shipment ID', copy=False)
    x_duoplane_shipment_posted = fields.Boolean(string='Posted to Duoplane', default=False, copy=False)
    x_duoplane_shipment_error = fields.Text(string='Duoplane Shipment Error', copy=False)
    x_duoplane_invoice_ids = fields.Many2many(
        'account.move',
        'duoplane_picking_invoice_rel',
        'picking_id',
        'move_id',
        string='Duoplane Auto Invoices',
        copy=False,
        help="Customer invoice(s) automatically created for THIS delivery after its Duoplane shipment post.",
    )

    def _action_done(self):
        res = super()._action_done()



        outgoing_pickings = self.filtered(lambda p: p.picking_type_id.code == 'outgoing')
        if outgoing_pickings:
            picking_ids = outgoing_pickings.ids

            def _postcommit_callback():
                # FIX: Use the registry to create a new cursor
                new_cr = self.env.registry.cursor()
                try:
                    # Create the new environment directly
                    new_env = api.Environment(new_cr, self.env.uid, self.env.context)

                    # Re-browse pickings with the NEW environment
                    pickings_to_process = new_env['stock.picking'].browse(picking_ids)

                    # Run the logic
                    pickings_to_process._process_duoplane_and_invoice_postcommit()

                    # Explicitly commit the new cursor's work
                    new_cr.commit()
                except Exception:
                    new_cr.rollback()
                    _logger.exception("Post-commit Duoplane/Invoice flow failed")
                finally:
                    # Crucial: Always close the cursor to avoid memory leaks/db hang
                    new_cr.close()

            self.env.cr.postcommit.add(_postcommit_callback)

        return res

    ### ADDED: New method that runs after commit (safe from rollback issues)
    def _process_duoplane_and_invoice_postcommit(self):
        for picking in self:
            if picking.picking_type_id.code != 'outgoing':
                continue

            try:
                # If already posted, don't repost — but DO allow invoice creation
                if not (picking.x_duoplane_shipment_posted and picking.x_duoplane_shipment_id):
                    result = picking._post_shipment_to_duoplane()
                else:
                    result = True  # treat as success so invoice logic can run

                # Create & POST invoice ONLY if duoplane post is done (posted + shipment_id)
                if result and picking.x_duoplane_shipment_posted and picking.x_duoplane_shipment_id:
                    picking._create_customer_invoice_after_duoplane_post(
                        post_invoice=True,
                        avoid_duplicates=True
                    )

            except Exception:
                # Never crash post-commit; log and keep delivery valid
                _logger.exception("Post-commit Duoplane/Invoice flow failed for picking %s", picking.name)

    def _create_customer_invoice_after_duoplane_post(self, post_invoice=False, avoid_duplicates=True):
        """
        Create a standard customer invoice for the related Sale Order.
        This runs ONLY after shipment was successfully posted to Duoplane.

        post_invoice=False  -> invoice is created as Draft
        post_invoice=True   -> invoice is created and Posted
        avoid_duplicates=True -> skip if THIS delivery already created an invoice.
                                 Each partial delivery still gets its own invoice for
                                 its own delivered quantity (standard Odoo _create_invoices
                                 only invoices not-yet-invoiced delivered qty).
        """
        self.ensure_one()

        sale_order = self._get_duoplane_sale_order()
        if not sale_order:
            return False

        # Avoid duplicates PER DELIVERY: only skip if THIS picking already
        # produced an invoice. A different (partial) delivery is allowed to
        # create its own invoice for its own delivered quantity.
        if avoid_duplicates:
            existing_for_picking = self.x_duoplane_invoice_ids.filtered(
                lambda m: m.move_type == 'out_invoice' and m.state in ('draft', 'posted')
            )
            if existing_for_picking:
                _logger.info(
                    "Picking %s already has auto invoice(s) %s, skipping (avoid_duplicates=True).",
                    self.name, existing_for_picking.ids
                )
                return False

        # Only create if there is something to invoice on the SO. After the first
        # partial delivery is invoiced, this flips back to 'to invoice' once the
        # next partial delivery is validated, so each partial gets its own invoice.
        if sale_order.invoice_status != 'to invoice':
            _logger.info(
                "SO %s invoice_status=%s, nothing new to invoice for picking %s.",
                sale_order.name, sale_order.invoice_status, self.name
            )
            return False

        # Create invoice using standard Odoo method (invoices only the
        # not-yet-invoiced delivered quantity).
        invoices = sale_order.with_context(default_move_type='out_invoice')._create_invoices()

        if not invoices:
            _logger.info("SO %s: _create_invoices() returned nothing.", sale_order.name)
            return False

        # Link the created invoice(s) to THIS delivery so we never invoice the
        # same delivery twice (post-commit re-run, manual retry, etc.).
        self.x_duoplane_invoice_ids = [(4, inv.id) for inv in invoices]

        if post_invoice:
            invoices.action_post()
            # Send email for each posted invoice
            for inv in invoices:
                if inv.state == 'posted':
                    self._send_invoice_email(inv)

        msg = "🧾 Customer invoice created automatically after Duoplane shipment post."
        if post_invoice:
            msg += " (Posted)"
        self.message_post(body=msg, message_type='notification')

        _logger.info("Created invoice(s) for SO %s: %s", sale_order.name, invoices.ids)
        return True

    def _send_invoice_email(self, invoice):
        """Finds the 'Invoice: Sending' template and sends it with the PDF attached."""
        self.ensure_one()
        if not invoice.partner_id.email:
            _logger.warning("Invoice %s: customer has no email.", invoice.name)
            return False

        # 1. Find your template
        template = self.env['mail.template'].search([
            ('name', '=', 'Invoice: Sending'),
            ('model_id.model', '=', 'account.move')
        ], limit=1)

        if not template:
            template = self.env.ref('account.email_template_edi_invoice', raise_if_not_found=False)

        if not template:
            _logger.error("✗ No email template found for Invoice %s", invoice.name)
            return False

        try:
            # FIX: Pass the template as the required argument
            invoice._generate_pdf_and_send_invoice(template)

            # 2. Send the mail immediately
            template.send_mail(
                invoice.id,
                force_send=True,
                raise_exception=True,
                email_values={'email_to': invoice.partner_id.email}
            )

            _logger.info("✓ Success: Invoice %s and PDF sent to %s", invoice.name, invoice.partner_id.email)
            return True
        except Exception as e:
            _logger.error("✗ Error sending automated email: %s", str(e))
            return False

    def _post_shipment_to_duoplane(self):
        """Post shipment information to Duoplane API"""
        self.ensure_one()

        # Skip if THIS delivery is already posted
        if self.x_duoplane_shipment_posted:
            _logger.info(f"Shipment already posted for {self.name}")
            self.message_post(
                body="ℹ️ Shipment already posted to Duoplane.",
                message_type='notification'
            )
            return True

        # Check for tracking number
        if not self.carrier_tracking_ref:
            _logger.info(f"No tracking number for {self.name}, skipping Duoplane shipment post")
            self.message_post(
                body="ℹ️ Duoplane shipment not posted - no tracking number provided.",
                message_type='notification'
            )
            return False

        # Get the related sale order
        sale_order = self._get_duoplane_sale_order()
        if not sale_order:
            _logger.info(f"No Duoplane sale order found for picking {self.name}")
            self.message_post(
                body="⚠️ No Duoplane sale order found for this delivery.",
                message_type='notification'
            )
            return False

        duoplane_po_id = sale_order.x_duoplane_po_id
        if not duoplane_po_id:
            _logger.info(f"No Duoplane PO ID on sale order {sale_order.name}")
            self.message_post(
                body=f"⚠️ No Duoplane PO ID found on sale order {sale_order.name}.",
                message_type='notification'
            )
            return False

        # Get Duoplane config
        config = self.env['duoplane.config'].search([('active', '=', True)], limit=1)
        if not config:
            _logger.warning("No active Duoplane configuration found")
            self.message_post(
                body="⚠️ No active Duoplane configuration found.",
                message_type='notification'
            )
            return False

        # Prepare shipment data - ONLY for items in THIS delivery
        shipment_data = self._prepare_duoplane_shipment_data_for_picking(sale_order)
        if not shipment_data:
            _logger.warning(f"Could not prepare shipment data for {self.name}")
            self.message_post(
                body="⚠️ Could not prepare shipment data. Check if order items have Duoplane IDs.",
                message_type='notification'
            )
            return False

        # Post to Duoplane API
        try:
            url = f"{config.api_url}/purchase_orders/{duoplane_po_id}/shipments.json"

            _logger.info(f"Posting shipment to Duoplane: {url}")
            _logger.info(f"Picking: {self.name}")
            _logger.info(f"Shipment data: {json.dumps(shipment_data, indent=2)}")

            response = requests.post(
                url,
                json=shipment_data,
                auth=HTTPBasicAuth(config.api_key, config.api_password),
                headers={
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                timeout=60
            )

            if response.status_code in [200, 201]:
                response_data = response.json()
                shipment_id = response_data.get('id') or response_data.get('shipment', {}).get('id')
                # --- CORRECTED LOGIC TO FIX THE PRICE OVERWRITE ---
                if sale_order and sale_order.carrier_id:
                    # 1. Find the delivery line on the SO
                    delivery_line = sale_order.order_line.filtered(lambda l: l.is_delivery)
                    if delivery_line:
                        # 2. Call the rating logic from the SALE ORDER'S carrier
                        # This triggers your 'rate_shipment' override with the Duoplane margin
                        res = sale_order.carrier_id.rate_shipment(sale_order)

                        if res.get('success'):
                            new_price = res.get('price')
                            # 3. Force the SO line back to our Margin Price
                            delivery_line.write({
                                'price_unit': new_price,
                            })
                            _logger.info("✓ Re-applied Duoplane margin to SO %s: New Price %s", sale_order.name,
                                         new_price)
                # --- END OF CORRECTED LOGIC ---

                # Mark THIS delivery as posted
                self.write({
                    'x_duoplane_shipment_posted': True,
                    'x_duoplane_shipment_id': shipment_id,
                    'x_duoplane_shipment_error': False,
                })

                _logger.info(
                    f"✓ Successfully posted shipment to Duoplane for {self.name}, Shipment ID: {shipment_id}")

                self.message_post(
                    body=f"✓ Shipment posted to Duoplane successfully.<br/>"
                         f"Shipment ID: {shipment_id}<br/>"
                         f"Items: {len(shipment_data['shipment']['shipment_items_attributes'])}",
                    message_type='notification'
                )
                return True
            else:
                error_msg = f"Duoplane API error {response.status_code}: {response.text}"
                _logger.error(error_msg)
                self.write({'x_duoplane_shipment_error': error_msg})
                self.message_post(
                    body=f"⚠️ Failed to post shipment to Duoplane: {error_msg}",
                    message_type='notification'
                )
                return False

        except requests.exceptions.RequestException as e:
            error_msg = f"Connection error posting to Duoplane: {str(e)}"
            _logger.error(error_msg)
            self.write({'x_duoplane_shipment_error': error_msg})
            self.message_post(
                body=f"⚠️ Failed to connect to Duoplane: {str(e)}",
                message_type='notification'
            )
            return False
        except Exception as e:
            error_msg = f"Error posting shipment: {str(e)}"
            _logger.error(error_msg)
            _logger.exception("Full traceback:")
            self.write({'x_duoplane_shipment_error': error_msg})
            self.message_post(
                body=f"⚠️ Error posting shipment: {str(e)}",
                message_type='notification'
            )
            return False

    def _get_duoplane_sale_order(self):
        """Get the related Duoplane sale order for this picking"""
        self.ensure_one()

        sale_order = None

        # Method 1: Via sale_id on picking (direct link)
        if self.sale_id:
            sale_order = self.sale_id
            _logger.info(f"Found SO via sale_id: {sale_order.name}")

        # Method 2: Via origin field
        if not sale_order and self.origin:
            sale_order = self.env['sale.order'].search([
                ('name', '=', self.origin)
            ], limit=1)
            if sale_order:
                _logger.info(f"Found SO via origin: {sale_order.name}")

        # Method 3: Via group_id
        if not sale_order and self.group_id:
            sale_order = self.env['sale.order'].search([
                ('procurement_group_id', '=', self.group_id.id)
            ], limit=1)
            if sale_order:
                _logger.info(f"Found SO via group_id: {sale_order.name}")

        # Method 4: Search via stock moves -> sale order line
        if not sale_order:
            for move in self.move_ids:
                if move.sale_line_id and move.sale_line_id.order_id:
                    sale_order = move.sale_line_id.order_id
                    _logger.info(f"Found SO via move: {sale_order.name}")
                    break

        # If no sale order found, return None
        if not sale_order:
            _logger.warning(f"No sale order found for picking {self.name}")
            return None

        # Check if SO has client_order_ref (Duoplane reference)
        if not sale_order.client_order_ref:
            _logger.warning(f"SO {sale_order.name} has no client_order_ref, not a Duoplane order")
            return None

        # If SO doesn't have x_duoplane_po_id OR order lines don't have order_item_ids, sync from Duoplane
        needs_sync = False

        if not sale_order.x_duoplane_po_id:
            _logger.info(f"SO {sale_order.name} missing x_duoplane_po_id, needs sync")
            needs_sync = True
        else:
            lines_without_item_id = sale_order.order_line.filtered(
                lambda l: not l.x_duoplane_order_item_id and l.product_id
            )
            if lines_without_item_id:
                _logger.info(
                    f"SO {sale_order.name} has {len(lines_without_item_id)} lines without order_item_id, needs sync")
                needs_sync = True

        if needs_sync:
            _logger.info(f"Syncing Duoplane data for SO {sale_order.name}")
            self._sync_duoplane_order_data(sale_order)

        # Final check - does it have Duoplane PO ID now?
        if not sale_order.x_duoplane_po_id:
            _logger.warning(f"SO {sale_order.name} still has no x_duoplane_po_id after sync")
            return None

        return sale_order

    # --- everything below is unchanged from your original (sync, shipment data, retry, etc.) ---

    def _sync_duoplane_order_data(self, sale_order):
        """
        Fetch Duoplane order data and update sale order with:
        - x_duoplane_po_id
        - x_duoplane_order_item_id on each line (matched by SKU)
        """
        if not sale_order.client_order_ref:
            _logger.warning(f"No client_order_ref on SO {sale_order.name}")
            return False

        config = self.env['duoplane.config'].search([('active', '=', True)], limit=1)
        if not config:
            _logger.warning("No active Duoplane configuration found")
            return False

        try:
            url = f"{config.api_url}/purchase_orders.json"
            params = {
                'search[public_reference]': sale_order.client_order_ref,
                'per_page': '1'
            }

            _logger.info(f"Searching Duoplane for order with reference: {sale_order.client_order_ref}")

            response = requests.get(
                url,
                params=params,
                auth=HTTPBasicAuth(config.api_key, config.api_password),
                headers={'Accept': 'application/json'},
                timeout=30
            )

            if response.status_code != 200:
                _logger.error(f"Duoplane API error: {response.status_code} - {response.text}")
                return False

            orders = response.json()
            if not orders or len(orders) == 0:
                _logger.warning(f"No Duoplane order found for reference {sale_order.client_order_ref}")
                return False

            duoplane_order = orders[0]
            duoplane_po_id = duoplane_order.get('id')

            sale_order.write({'x_duoplane_po_id': duoplane_po_id})
            _logger.info(f"Updated SO {sale_order.name} with x_duoplane_po_id: {duoplane_po_id}")

            detail_url = f"{config.api_url}/purchase_orders/{duoplane_po_id}.json"
            _logger.info(f"Fetching order details from: {detail_url}")

            detail_response = requests.get(
                detail_url,
                auth=HTTPBasicAuth(config.api_key, config.api_password),
                headers={'Accept': 'application/json'},
                timeout=30
            )

            if detail_response.status_code != 200:
                _logger.error(f"Duoplane API error fetching order details: {detail_response.status_code}")
                return True

            order_data = detail_response.json()
            order_items = order_data.get('order_items', [])

            _logger.info(f"Found {len(order_items)} order items in Duoplane")

            sku_to_item_id = {}
            for item in order_items:
                vendor_sku = item.get('vendor_sku')
                retailer_sku = item.get('retailer_sku')
                item_id = item.get('id')

                if vendor_sku:
                    sku_to_item_id[vendor_sku] = item_id
                    _logger.info(f"  Mapped vendor_sku '{vendor_sku}' -> order_item_id {item_id}")
                if retailer_sku:
                    sku_to_item_id[retailer_sku] = item_id
                    _logger.info(f"  Mapped retailer_sku '{retailer_sku}' -> order_item_id {item_id}")

            _logger.info(f"SKU to order_item_id mapping: {sku_to_item_id}")

            updated_count = 0
            for line in sale_order.order_line:
                if line.x_duoplane_order_item_id:
                    _logger.info(f"Line {line.id} already has order_item_id: {line.x_duoplane_order_item_id}")
                    continue

                product = line.product_id
                sku = product.default_code
                retailer_sku = getattr(product, 'x_retailer_sku', None)

                _logger.info(
                    f"Processing line {line.id}: product={product.name}, sku={sku}, retailer_sku={retailer_sku}"
                )

                order_item_id = None
                if sku and sku in sku_to_item_id:
                    order_item_id = sku_to_item_id[sku]
                    _logger.info(f"  Matched by vendor SKU: {sku}")
                elif retailer_sku and retailer_sku in sku_to_item_id:
                    order_item_id = sku_to_item_id[retailer_sku]
                    _logger.info(f"  Matched by retailer SKU: {retailer_sku}")

                if order_item_id:
                    line.write({'x_duoplane_order_item_id': order_item_id})
                    updated_count += 1
                    _logger.info(f"✓ Updated line {line.id} (SKU: {sku}) with order_item_id: {order_item_id}")
                else:
                    _logger.warning(
                        f"✗ Could not find order_item_id for line {line.id}, SKU: {sku}, retailer_sku: {retailer_sku}"
                    )

            _logger.info(
                f"Updated {updated_count} of {len(sale_order.order_line)} order lines with Duoplane order_item_ids"
            )

            return True

        except Exception as e:
            _logger.error(f"Error syncing Duoplane order data: {str(e)}")
            _logger.exception("Full traceback:")
            return False

    def _get_shipping_cost(self, sale_order):
        self.ensure_one()

        # 1. Try the standard Odoo carrier_price field on the picking
        if hasattr(self, 'carrier_price') and self.carrier_price:
            _logger.info(f"Found cost on picking via ORM: {self.carrier_price}")
            return float(self.carrier_price)

        # 2. Fallback: If picking cost is 0, check the Sale Order shipping line
        # (This is useful if you use flat rates or margins calculated at the SO level)
        shipping_line = sale_order.order_line.filtered(lambda l: l.is_delivery)
        if shipping_line:
            # Use price_subtotal to get the cost before taxes
            _logger.info(f"Found cost via SO shipping line: {shipping_line[0].price_subtotal}")
            return shipping_line[0].price_subtotal

        return 0.0

    def _prepare_duoplane_shipment_data_for_picking(self, sale_order):
        self.ensure_one()

        shipper_name = 'Other'
        if self.carrier_id:
            shipper_name = self.carrier_id.name

        carrier_mapping = {
            'ups': 'UPS',
            'ups us': 'UPS',
            'fedex': 'FedEx',
            'usps': 'USPS',
            'dhl': 'DHL',
            'ontrac': 'OnTrac',
        }
        shipper_name_lower = shipper_name.lower()
        for key, value in carrier_mapping.items():
            if key in shipper_name_lower:
                shipper_name = value
                break

        _logger.info(f"Preparing shipment data for {self.name}, carrier: {shipper_name}")

        shipment_items = []
        items_by_order_item_id = {}

        for move in self.move_ids.filtered(lambda m: m.state == 'done'):
            sale_line = move.sale_line_id

            if not sale_line:
                sale_line = sale_order.order_line.filtered(
                    lambda l: l.product_id == move.product_id
                )[:1]

            if sale_line and sale_line.x_duoplane_order_item_id:
                order_item_id = sale_line.x_duoplane_order_item_id
                done_qty = move.quantity  # Odoo 17 done qty

                if order_item_id in items_by_order_item_id:
                    items_by_order_item_id[order_item_id] += done_qty
                else:
                    items_by_order_item_id[order_item_id] = done_qty

                _logger.info(
                    f"Move {move.id}: Product {move.product_id.default_code}, "
                    f"order_item_id={order_item_id}, qty={done_qty}"
                )
            else:
                _logger.warning(
                    f"Could not find Duoplane order_item_id for move {move.id}, "
                    f"product {move.product_id.default_code}"
                )

        for order_item_id, quantity in items_by_order_item_id.items():
            if quantity > 0:
                shipment_items.append({
                    'order_item_id': order_item_id,
                    'quantity': int(quantity)
                })

        if not shipment_items:
            _logger.warning(f"No shipment items found for picking {self.name}")
            return None

        tracking_numbers = []
        if self.carrier_tracking_ref:
            tracking_refs = self.carrier_tracking_ref.replace('\n', ',').split(',')
            for ref in tracking_refs:
                ref = ref.strip()
                if ref:
                    tracking_numbers.append({'tracking': ref})

        shipping_cost = self._get_shipping_cost(sale_order)

        shipment_data = {
            'shipment': {
                'shipper_name': shipper_name,
                'shipment_items_attributes': shipment_items,
                'vendor_shipping_cost': f"{shipping_cost:.2f}",
            }
        }

        if tracking_numbers:
            shipment_data['shipment']['shipment_trackings_attributes'] = tracking_numbers

        _logger.info(
            f"Prepared shipment for {self.name}: {len(shipment_items)} items, "
            f"{len(tracking_numbers)} tracking numbers, shipping_cost: {shipping_cost}"
        )

        return shipment_data

    def action_retry_duoplane_shipment(self):
        self.ensure_one()

        if self.state != 'done':
            raise UserError(_("Can only post shipments for validated deliveries."))

        if self.x_duoplane_shipment_posted:
            raise UserError(_("Shipment already posted to Duoplane."))

        result = self._post_shipment_to_duoplane()

        if result and self.x_duoplane_shipment_posted and self.x_duoplane_shipment_id:
            self._create_customer_invoice_after_duoplane_post(
                post_invoice=True,
                avoid_duplicates=True
            )

        if result:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('Shipment posted to Duoplane successfully!'),
                    'type': 'success',
                    'sticky': False,
                }
            }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Error'),
                    'message': _('Failed to post shipment. Check the error details on the delivery.'),
                    'type': 'danger',
                    'sticky': True,
                }
            }
