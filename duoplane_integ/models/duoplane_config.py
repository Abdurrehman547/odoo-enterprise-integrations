# -*- coding: utf-8 -*-
import requests
from requests.auth import HTTPBasicAuth
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class DuoplaneConfig(models.Model):
    _name = 'duoplane.config'
    _description = 'Duoplane Configuration'
    _rec_name = 'name'

    name = fields.Char(string='Configuration Name', required=True, default='Duoplane API')
    active = fields.Boolean(string='Active', default=True)

    # API Credentials
    api_url = fields.Char(
        string='API URL',
        required=True,
        default='https://app.duoplane.com',
        help='Duoplane API base URL'
    )
    api_key = fields.Char(string='API Key', required=True, help='Your Duoplane API Key')
    api_password = fields.Char(string='API Password', required=True, help='Your Duoplane API Password')

    # Sync Settings
    auto_create_products = fields.Boolean(
        string='Auto Create Products',
        default=True,
        help='Automatically create products if they do not exist'
    )
    auto_create_partners = fields.Boolean(
        string='Auto Create Customers',
        default=True,
        help='Automatically create customer records from shipping addresses'
    )
    default_product_type = fields.Selection(
        [('product', 'Storable Product'),
         ('consu', 'Consumable')],
        string='Default Product Type',
        default='product',
        required=True
    )
    default_product_categ_id = fields.Many2one(
        'product.category',
        string='Default Product Category',
        help='Default category for auto-created products'
    )
    delivery_carrier_id = fields.Many2one(
        'delivery.carrier',
        string='Shipping Method',
        help='Shipping method automatically applied as a delivery line on sale '
             'orders fetched from Duoplane.'
    )

    # Sync Filters
    sync_date_from = fields.Datetime(
        string='Sync From Date',
        default=fields.Datetime.now,
        help='Only sync orders updated after this date'
    )
    sync_unfulfilled_only = fields.Boolean(
        string='Sync Unfulfilled Orders Only',
        default=True,
        help='Only import orders that are not yet fulfilled'
    )

    # Status
    last_sync_date = fields.Datetime(string='Last Sync Date', readonly=True)
    last_sync_status = fields.Selection(
        [('success', 'Success'),
         ('error', 'Error'),
         ('partial', 'Partial Success')],
        string='Last Sync Status',
        readonly=True
    )
    last_sync_message = fields.Text(string='Last Sync Message', readonly=True)

    # Statistics
    total_orders_synced = fields.Integer(string='Total Orders Synced', readonly=True, default=0)
    total_products_created = fields.Integer(string='Total Products Created', readonly=True, default=0)
    total_partners_created = fields.Integer(string='Total Partners Created', readonly=True, default=0)

    @api.constrains('api_key', 'api_password')
    def _check_credentials(self):
        """Validate API credentials"""
        for record in self:
            if record.api_key and record.api_password:
                if len(record.api_key) < 10:
                    raise ValidationError(_('API Key seems invalid. Please check your credentials.'))

    def action_test_connection(self):
        """Test Duoplane API connection"""
        self.ensure_one()

        try:
            url = f"{self.api_url}/purchase_orders.json"
            params = {
                'search[updated_at_min]': fields.Datetime.to_string(fields.Datetime.now()),
                'per_page': '1'
            }

            response = requests.get(
                url,
                params=params,
                auth=HTTPBasicAuth(self.api_key, self.api_password),
                headers={'Accept': 'application/json'},
                timeout=30
            )

            if response.status_code == 200:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Connection to Duoplane API successful!'),
                        'type': 'success',
                        'sticky': False,
                        'next': {'type': 'ir.actions.act_window_close'},
                    }
                }
            elif response.status_code == 401:
                raise UserError(_('Authentication failed. Please check your API credentials.'))
            else:
                raise UserError(_('Connection failed with status code: %s') % response.status_code)

        except requests.exceptions.RequestException as e:
            raise UserError(_('Connection error: %s') % str(e))

    def action_sync_orders(self):
        """Manual sync action"""
        self.ensure_one()
        return self.env['duoplane.sync.wizard'].create({
            'config_id': self.id
        }).action_sync()

    def _fetch_duoplane_orders(self):
        """Fetch orders from Duoplane API"""
        self.ensure_one()

        url = f"{self.api_url}/purchase_orders.json"
        params = {
            'search[updated_at_min]': fields.Datetime.to_string(self.sync_date_from),
            'per_page': '250',
            'page': '1'
        }

        if self.sync_unfulfilled_only:
            params['search[fulfilled]'] = '0'

        try:
            response = requests.get(
                url,
                params=params,
                auth=HTTPBasicAuth(self.api_key, self.api_password),
                headers={'Accept': 'application/json'},
                timeout=60
            )

            if response.status_code == 200:
                return response.json()
            else:
                _logger.error(f"Duoplane API error: {response.status_code} - {response.text}")
                return []

        except Exception as e:
            _logger.error(f"Error fetching Duoplane orders: {str(e)}")
            return []

    def cron_sync_orders(self):
        """Scheduled action to sync orders"""
        configs = self.search([('active', '=', True)])
        for config in configs:
            try:
                config.sync_orders()
            except Exception as e:
                _logger.error(f"Error in scheduled Duoplane sync: {str(e)}")

    def sync_orders(self):
        """Main sync method"""
        self.ensure_one()

        _logger.info("Starting Duoplane order sync...")

        orders = self._fetch_duoplane_orders()

        if not orders:
            self.write({
                'last_sync_date': fields.Datetime.now(),
                'last_sync_status': 'success',
                'last_sync_message': 'No new orders to sync'
            })
            return {
                'orders_created': 0,
                'products_created': 0,
                'partners_created': 0,
                'errors': 0
            }

        stats = {
            'orders_created': 0,
            'products_created': 0,
            'partners_created': 0,
            'errors': 0,
            'error_messages': []
        }

        for duoplane_order in orders:
            # Use savepoint for each order to isolate transactions
            try:
                with self.env.cr.savepoint():
                    result = self._create_sale_order_from_duoplane(duoplane_order)
                    if result:
                        stats['orders_created'] += 1
                        stats['products_created'] += result.get('products_created', 0)
                        stats['partners_created'] += result.get('partners_created', 0)
            except Exception as e:
                stats['errors'] += 1
                error_msg = f"Order {duoplane_order.get('public_reference')}: {str(e)}"
                stats['error_messages'].append(error_msg)
                _logger.error(error_msg)
                _logger.exception("Full error details:")

        # Update statistics
        self.write({
            'last_sync_date': fields.Datetime.now(),
            'last_sync_status': 'success' if stats['errors'] == 0 else (
                'partial' if stats['orders_created'] > 0 else 'error'),
            'last_sync_message': self._format_sync_message(stats),
            'total_orders_synced': self.total_orders_synced + stats['orders_created'],
            'total_products_created': self.total_products_created + stats['products_created'],
            'total_partners_created': self.total_partners_created + stats['partners_created'],
        })

        _logger.info(f"Duoplane sync completed: {stats}")

        return stats

    def _format_sync_message(self, stats):
        """Format sync statistics message"""
        msg = f"Orders: {stats['orders_created']}, "
        msg += f"Products: {stats['products_created']}, "
        msg += f"Partners: {stats['partners_created']}, "
        msg += f"Errors: {stats['errors']}"

        if stats['error_messages']:
            msg += "\n\nErrors:\n" + "\n".join(stats['error_messages'][:5])
            if len(stats['error_messages']) > 5:
                msg += f"\n... and {len(stats['error_messages']) - 5} more errors"

        return msg

    def _create_sale_order_from_duoplane(self, duoplane_order):
        """Create Odoo sale order from Duoplane data"""
        self.ensure_one()

        duoplane_id = duoplane_order.get('id')
        retailer_name = duoplane_order.get('retailer', {}).get('name')
        public_ref = duoplane_order.get('public_reference')

        # ---------------------------------------------------------
        # SET TARGET COMPANY: Force everything to Texas
        # ---------------------------------------------------------
        target_company = self.env['res.company'].sudo().search([
            ('name', 'ilike', 'Andline Distribution LLC')
        ], limit=1)

        if not target_company:
            raise UserError("CRITICAL ERROR: Could not find company 'Andline Distribution LLC'. Sync aborted.")

        company_id_to_use = target_company.id

        # ---------------------------------------------------------
        # GLOBAL DUPLICATE CHECK:
        # Use .sudo() to bypass multi-company record rules.
        # This checks if the ID exists in TEXAS OR ANDLINE.
        # ---------------------------------------------------------
        existing_order = self.env['sale.order'].sudo().search([
            '|',
            ('x_duoplane_po_id', '=', duoplane_id),
            ('client_order_ref', '=', public_ref)
        ], limit=1)

        if existing_order:
            _logger.info(f"SKIPPING: Order {public_ref} already exists in {existing_order.company_id.name}")
            return None

        result = {
            'products_created': 0,
            'partners_created': 0
        }

        # Get VRW company and create/get delivery address
        vrw_company, delivery_address = self._get_or_create_partner(duoplane_order.get('shipping_address', {}))

        if not vrw_company:
            raise UserError(_('No shipping address provided for order %s') % public_ref)

        if not delivery_address:
            raise UserError(_('Could not create delivery address for order %s (missing shipping data)') % public_ref)

        # Count if delivery address was just created
        if delivery_address and delivery_address.create_date >= fields.Datetime.now().replace(second=0, microsecond=0):
            result['partners_created'] = 1

        # Prepare order lines
        order_lines = []
        for item in duoplane_order.get('order_items', []):
            product, is_new = self._get_or_create_product(item, retailer_name)
            if product:
                if is_new:
                    result['products_created'] += 1

                duoplane_order_item_id = item.get('id')

                order_lines.append((0, 0, {
                    'product_id': product.id,
                    'product_uom_qty': item.get('quantity', 1),
                    'price_unit': item.get('price') or item.get('cost') or 0,
                    'name': item.get('name', product.name),
                    'x_duoplane_order_item_id': duoplane_order_item_id,
                }))

        if not order_lines:
            raise UserError(_('No valid order lines for order %s') % public_ref)

        # Create sale order
        order_vals = {
            'company_id': company_id_to_use,
            'partner_id': vrw_company.id,
            'partner_shipping_id': delivery_address.id,
            'client_order_ref': public_ref,
            'x_duoplane_po_id': duoplane_id,
            'x_retailer': retailer_name,
            'commitment_date': duoplane_order.get('promise_date'),
            'order_line': order_lines,
            'note': f"Imported from Duoplane\nRetailer: {retailer_name or 'Unknown'}\nShipping To: {delivery_address.name}",
        }

        # Apply the shipping method selected in the Duoplane configuration so the
        # auto delivery line uses it when the order is confirmed below.
        if self.delivery_carrier_id:
            order_vals['carrier_id'] = self.delivery_carrier_id.id

        # Use sudo() to ensure creation happens even if current user has rule restrictions
        sale_order = self.env['sale.order'].sudo().create(order_vals)
        _logger.info(f"Created sale order {sale_order.name} from Duoplane PO {public_ref}")

        # Confirm the sale order
        try:
            sale_order.action_confirm()
            _logger.info(f"✓ Auto-confirmed sale order {sale_order.name}")
        except Exception as e:
            _logger.warning(f"Could not auto-confirm order {sale_order.name}: {str(e)}")

        return result

    def _get_or_create_partner(self, shipping_address):
        """Get VRW company and create/get delivery address under it"""
        self.ensure_one()

        if not shipping_address:
            return None, None

        vrw_company = self.env['res.partner'].search([
            ('name', '=', 'VRW'),
            ('is_company', '=', True),
            ('active', '=', True)
        ], limit=1)

        if not vrw_company:
            raise UserError(_('VRW company not found. Please create it in Contacts first.'))

        first_name = shipping_address.get('first_name', '').strip()
        last_name = shipping_address.get('last_name', '').strip()
        delivery_name = f"{first_name} {last_name}".strip().upper()

        if not delivery_name:
            company_name = shipping_address.get('company_name', '').strip()
            delivery_name = company_name if company_name else 'Unknown Customer'

        street = shipping_address.get('address_1', '').strip()
        zip_code = shipping_address.get('post_code', '').strip()

        delivery_address = self.env['res.partner'].search([
            ('parent_id', '=', vrw_company.id),
            ('type', '=', 'delivery'),
            ('name', '=', delivery_name),
        ], limit=1)

        if delivery_address:
            return vrw_company, delivery_address

        if not self.auto_create_partners:
            return vrw_company, None

        country_code = shipping_address.get('country_iso2', 'US')
        country = self.env['res.country'].search([('code', '=', country_code)], limit=1)

        state = False
        province_iso = shipping_address.get('province_iso', '').strip()
        if province_iso and country:
            state = self.env['res.country.state'].search([
                ('code', '=', province_iso),
                ('country_id', '=', country.id)
            ], limit=1)

        delivery_vals = {
            'parent_id': vrw_company.id,
            'name': delivery_name,
            'street': street,
            'street2': shipping_address.get('address_2', ''),
            'city': shipping_address.get('city', ''),
            'state_id': state.id if state else False,
            'zip': zip_code,
            'country_id': country.id if country else False,
            'phone': '+1 631 540 1261',
            'email': shipping_address.get('email', ''),
            'type': 'delivery',
            'is_company': False,
            'company_type': 'person',
            'x_duoplane_customer': True,
            'comment': f'Duoplane delivery address - Company: {shipping_address.get("company_name", "")}',
        }

        delivery_address = self.env['res.partner'].with_context(tracking_disable=True).create(delivery_vals)
        return vrw_company, delivery_address

    def _get_or_create_product(self, order_item, retailer_name=None):
        """
        STRICT LOOKUP: Only search by Vendor SKU -> Odoo Internal Reference.
        Do NOT create products. Do NOT fallback to Retailer SKU.
        """
        self.ensure_one()

        # 1. Get the Vendor SKU from Duoplane
        vendor_sku = order_item.get('vendor_sku')

        # Logging for debug
        _logger.info(f"Processing item: {order_item.get('name')} | Vendor SKU: {vendor_sku}")

        if not vendor_sku:
            _logger.warning("SKIP: Item has no Vendor SKU from Duoplane.")
            return None, False

        # 2. STRICT SEARCH: Compare Duoplane Vendor SKU with Odoo Internal Reference
        # We use .strip() to avoid errors with accidental spaces
        product = self.env['product.product'].search([
            ('default_code', '=', vendor_sku.strip())
        ], limit=1)

        if product:
            _logger.info(f"✓ MATCH: Found product {product.name} (ID: {product.id})")

            # Optional: Sync Retailer SKU for future reference (safe to keep)
            retailer_sku = order_item.get('retailer_sku')
            if retailer_sku and not product.x_retailer_sku:
                product.write({'x_retailer_sku': retailer_sku})

            return product, False

        else:
            # 3. NO MATCH FOUND -> STOP
            # Do not create. Do not check Retailer SKU.
            _logger.warning(f"⛔ NO MATCH: Vendor SKU '{vendor_sku}' not found in Odoo. Skipping item.")
            return None, False

    def _get_or_create_vendor(self, retailer_name):
        """Get or create vendor partner from retailer name"""
        if not retailer_name:
            return None

        vendor = self.env['res.partner'].search([
            ('name', '=', retailer_name),
            ('supplier_rank', '>', 0)
        ], limit=1)

        if vendor:
            return vendor

        vendor = self.env['res.partner'].create({
            'name': retailer_name,
            'supplier_rank': 1,
            'company_type': 'company',
            'comment': 'Auto-created from Duoplane integration',
        })
        return vendor

    def _create_product_vendor(self, product, retailer_name, order_item):
        """Create vendor/supplier info for product"""
        try:
            vendor = self._get_or_create_vendor(retailer_name)
            if not vendor:
                return

            existing_supplier = self.env['product.supplierinfo'].search([
                ('partner_id', '=', vendor.id),
                ('product_tmpl_id', '=', product.product_tmpl_id.id),
            ], limit=1)

            if existing_supplier:
                return

            cost = float(order_item.get('cost', 0) or order_item.get('price', 0) or 0)

            self.env['product.supplierinfo'].create({
                'partner_id': vendor.id,
                'product_tmpl_id': product.product_tmpl_id.id,
                'product_id': product.id,
                'price': cost,
                'min_qty': 1.0,
                'delay': 1,
            })
        except Exception as e:
            _logger.error(f"Error creating vendor info: {str(e)}")

    def _ensure_product_vendor(self, product, retailer_name, order_item):
        """Ensure product has vendor information"""
        try:
            vendor = self._get_or_create_vendor(retailer_name)
            if not vendor:
                return

            existing_supplier = self.env['product.supplierinfo'].search([
                ('partner_id', '=', vendor.id),
                ('product_tmpl_id', '=', product.product_tmpl_id.id),
            ], limit=1)

            if not existing_supplier:
                cost = float(order_item.get('cost', 0) or order_item.get('price', 0) or 0)
                self.env['product.supplierinfo'].create({
                    'partner_id': vendor.id,
                    'product_tmpl_id': product.product_tmpl_id.id,
                    'product_id': product.id,
                    'price': cost,
                    'min_qty': 1.0,
                    'delay': 1,
                })
        except Exception as e:
            _logger.error(f"Error ensuring vendor info: {str(e)}")