# -*- coding: utf-8 -*-
import logging
from datetime import datetime, time as dt_time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

from odoo.addons.bha_synapse.models.synapse_connection import SYNAPSE_ORDER_STATUSES

# Daily Synapse report (see cron_send_synapse_daily_report): timezone used to
# bucket orders into a calendar "day" regardless of the server's own timezone.
# The recipient lives on the mail.template itself (email_to), editable from
# Settings > Technical > Email Templates, not here.
SYNAPSE_REPORT_TZ = "America/Los_Angeles"
# Aging-orders section can grow unbounded over time; cap what is listed in the
# email body (oldest first) while still reporting the true total in the title.
SYNAPSE_REPORT_MAX_AGING_ROWS = 150


class SaleOrder(models.Model):
    _inherit = "sale.order"

    synapse_connection_id = fields.Many2one(
        "synapse.connection", string="Synapse Connection", copy=False
    )
    synapse_orderid = fields.Char(string="Synapse Order ID", copy=False, readonly=True)
    synapse_shipid = fields.Char(string="Synapse Ship ID", copy=False, readonly=True)
    synapse_wave = fields.Char(string="Synapse Wave", copy=False, readonly=True)
    synapse_status = fields.Selection(
        [
            ("draft", "To Send"),
            ("sent", "Sent"),
            ("failed", "Failed"),
            ("shipped", "Shipped"),
            ("cancelled", "Cancelled"),
        ],
        string="Synapse Status",
        copy=False,
        help="High-level Odoo-side tracking of the order's Synapse lifecycle.",
    )
    synapse_order_status = fields.Selection(
        SYNAPSE_ORDER_STATUSES,
        string="Synapse Order Status",
        copy=False,
        readonly=True,
        help="The granular order status reported by Synapse itself "
        "(order-info), e.g. Hold / Released / Picking / Shipped. Use "
        "'Refresh Synapse Status' to update it.",
    )
    synapse_to_send = fields.Boolean(
        string="Send to Synapse",
        copy=False,
        help="When set, the scheduled job will push this order to Synapse.",
    )
    synapse_shipped_date = fields.Date(
        string="Synapse Shipped Date",
        copy=False,
        readonly=True,
        help="Date this order first turned 'Shipped' (from the Synapse "
        "shipment record). Stamped once, on the first shipment received for "
        "the order — later edits to the order do not move it. Used by the "
        "daily Synapse report to find orders shipped 'today'.",
    )
    synapse_customer_packing_list = fields.Boolean(
        string="Customer Packing List",
        help="When set, the customer supplies their own packing list: no "
        "Packing Slip is generated or sent to Synapse for this order. "
        "'Upload Labels to Synapse' then only sends the shipping labels and "
        "any manually attached files (e.g. the customer's packing list "
        "attached to the order).",
    )
    synapse_shipment_ids = fields.One2many(
        "synapse.shipment", "sale_order_id", string="Synapse Shipments"
    )
    synapse_shipment_count = fields.Integer(
        compute="_compute_synapse_shipment_count", string="Shipments"
    )

    def _synapse_shipment_domain(self):
        """Shipments belonging to this order: the stored link plus a live
        fallback on the Synapse order id (covers shipments imported before
        the link was established)."""
        self.ensure_one()
        domain = [("sale_order_id", "=", self.id)]
        if self.synapse_orderid:
            domain = [
                "|",
                ("sale_order_id", "=", self.id),
                ("orderid", "=", self.synapse_orderid),
            ]
        return domain

    @api.depends("synapse_orderid")
    def _compute_synapse_shipment_count(self):
        Shipment = self.env["synapse.shipment"]
        for order in self:
            order.synapse_shipment_count = Shipment.search_count(
                order._synapse_shipment_domain()
            )

    def action_view_synapse_shipments(self):
        """Smart button: open the Synapse shipments of this order."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Synapse Shipments"),
            "res_model": "synapse.shipment",
            "view_mode": "list,form",
            "domain": self._synapse_shipment_domain(),
            "target": "current",
        }

    # ------------------------------------------------------------------
    # Automatic fulfilment decision (Synapse stock -> Delivery vs RFQ)
    # ------------------------------------------------------------------
    def action_confirm(self):
        """On confirmation, decide per product line how it will be fulfilled.

        For every physical product line, check the Synapse (Freeport) inventory
        snapshot: if enough is on-hand, force the warehouse delivery route so a
        Delivery Order is created (fulfil from Synapse stock); otherwise force
        the Dropship route so an RFQ/Purchase Order is raised automatically.
        This applies to ALL products, not only those on the Dropship route.
        Orders with no active Synapse connection, no dropship route, or no
        delivery route confirm unchanged.
        """
        for order in self:
            order._synapse_route_order_lines()
        return super().action_confirm()

    def _synapse_connection_soft(self):
        """Active Synapse connection for this order, or an empty recordset.

        Unlike ``_synapse_connection`` this never raises, so confirmation of a
        non-Synapse order is never blocked."""
        return self.synapse_connection_id or self.env["synapse.connection"].search(
            [("active", "=", True)], limit=1
        )

    def _synapse_route_order_lines(self):
        """Route every physical product line to delivery or purchase based on
        combined on-hand stock (Synapse snapshot + Odoo's own inventory,
        excluding the Synapse mirror location). See ``action_confirm``."""
        self.ensure_one()
        dropship_route = self.env.ref(
            "stock_dropshipping.route_drop_shipping", raise_if_not_found=False
        )
        if not dropship_route:
            return
        connection = self._synapse_connection_soft()
        if not connection:
            return
        delivery_route = self.warehouse_id.delivery_route_id
        if not delivery_route:
            return
        synapse_map = connection._synapse_available_map()
        Connection = self.env["synapse.connection"]
        decisions = []
        for line in self.order_line:
            if not Connection._is_pickable_line(line):
                continue
            # Only act on dropship-marked products; every other line keeps
            # Odoo's default fulfilment untouched (no stock check, no reroute).
            is_dropship = (
                line.route_id == dropship_route
                or dropship_route in line.product_id.route_ids
            )
            if not is_dropship:
                continue
            sku = line.product_id.default_code
            # Combined availability: Synapse on-hand (snapshot) + Odoo's own
            # on-hand. When the SKU is unknown to Synapse, synapse_qty is 0 and
            # the decision falls back to Odoo stock only.
            synapse_qty = synapse_map.get(sku, 0.0) if sku else 0.0
            odoo_qty = self._odoo_free_excl_synapse(line.product_id, connection)
            on_hand = synapse_qty + odoo_qty
            demand = line.product_uom_qty
            if on_hand >= demand:
                # Enough combined stock -> deliver the whole line from stock.
                if line.route_id != delivery_route:
                    line.route_id = delivery_route.id
                decisions.append(
                    _("• %(prod)s: %(qty)s available (Synapse %(s)s + Odoo "
                      "%(o)s) → Delivery Order")
                    % {"prod": line.product_id.display_name, "qty": "%g" % demand,
                       "s": "%g" % synapse_qty, "o": "%g" % odoo_qty}
                )
            elif not line.product_id.seller_ids:
                # Short, and NO vendor -> leave Odoo's default flow untouched
                # (no RFQ is raised for an unpurchasable product).
                decisions.append(
                    _("• %(prod)s: %(qty)s available (Synapse %(s)s + Odoo "
                      "%(o)s), no vendor → left to Odoo's default flow (no RFQ)")
                    % {"prod": line.product_id.display_name, "qty": "%g" % on_hand,
                       "s": "%g" % synapse_qty, "o": "%g" % odoo_qty}
                )
            elif on_hand > 0:
                # Partly available: deliver what is on-hand (Synapse + Odoo) and
                # buy ONLY the shortfall. Split the line — the available qty stays
                # on this line (delivery route); the missing qty moves to a new
                # line on the Dropship route, so the RFQ covers just the shortfall.
                shortfall = demand - on_hand
                price, discount = line.price_unit, line.discount
                line.product_uom_qty = on_hand
                line.route_id = delivery_route.id
                line.price_unit, line.discount = price, discount
                line.copy({
                    "product_uom_qty": shortfall,
                    "route_id": dropship_route.id,
                    "price_unit": price,
                    "discount": discount,
                })
                decisions.append(
                    _("• %(prod)s: %(av)s available (Synapse %(s)s + Odoo %(o)s) "
                      "→ Delivery; %(sh)s short → RFQ/Purchase Order")
                    % {"prod": line.product_id.display_name,
                       "av": "%g" % on_hand, "s": "%g" % synapse_qty,
                       "o": "%g" % odoo_qty, "sh": "%g" % shortfall}
                )
            else:
                # Nothing on Synapse or in Odoo -> buy the whole demand via RFQ/PO.
                if line.route_id != dropship_route:
                    line.route_id = dropship_route.id
                decisions.append(
                    _("• %(prod)s: 0 available (Synapse + Odoo) → RFQ/Purchase Order")
                    % {"prod": line.product_id.display_name}
                )
        if decisions:
            self.message_post(
                body=_("Fulfilment decision (Synapse + Odoo stock):<br/>%s")
                % "<br/>".join(decisions)
            )

    def _odoo_free_excl_synapse(self, product, connection):
        """Odoo's own available quantity (on-hand − reserved) for ``product``
        across the company's internal locations, EXCLUDING the Synapse reconcile
        location.

        The Synapse snapshot quantity is added separately by the caller. Because
        ``auto_reconcile_inventory`` mirrors Synapse stock into that Odoo
        location (e.g. WH/Phoenix), it must be excluded here so the Synapse
        stock is not counted twice.
        """
        self.ensure_one()
        domain = [
            ("product_id", "=", product.id),
            ("location_id.usage", "=", "internal"),
            ("company_id", "in", [self.company_id.id, False]),
        ]
        synapse_loc = connection.inventory_location_id
        if synapse_loc:
            domain += ["!", ("location_id", "child_of", synapse_loc.id)]
        quants = self.env["stock.quant"].sudo().search(domain)
        return sum(quants.mapped("available_quantity"))

    def _synapse_connection(self):
        return self.synapse_connection_id or self.env[
            "synapse.connection"
        ]._get_default_connection()

    def _notify_queued(self, message):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"type": "success", "sticky": False, "message": message},
        }

    def _synapse_carrier_codes(self):
        """Return (shipping_service, carrier_code) derived from the SO's
        delivery carrier, or (None, None) when none is set."""
        carrier = getattr(self, "carrier_id", False)
        if not carrier:
            return None, None
        shipping_service = carrier.synapse_service_code or carrier.name
        return shipping_service or None, carrier.synapse_carrier_code or None

    def action_send_to_synapse(self):
        """Open the wizard to review and push this order to Synapse.

        Entry point on the Sales Order (the Delivery Order has its own button).
        The wizard derives lines, shipping method, terms and PO# from the
        order itself."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Send to Synapse"),
            "res_model": "synapse.order.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_sale_order_id": self.id},
        }

    def action_update_synapse(self):
        """Push header-level changes (carrier / shipping / routing) of an
        already-sent order to Synapse via update-order. Does NOT change lines,
        quantities, or the ship-to address — use 'Resync Order' for those."""
        self.ensure_one()
        if not self.synapse_orderid:
            raise UserError(
                _("Order %s has not been sent to Synapse yet; nothing to update.")
                % self.name
            )
        connection = self._synapse_connection()
        shipping_service, carrier_code = self._synapse_carrier_codes()
        # Nothing to send if the order has no delivery carrier and the
        # connection has no default shipping service — tell the user now
        # instead of letting the queue job fail later.
        if not shipping_service and not carrier_code and not (
            connection.default_shipping_service
        ):
            raise UserError(
                _(
                    "Order %s has no Delivery Method, so there is no carrier or "
                    "shipping service to update in Synapse. Set a Delivery Method "
                    "on the order first (or a Default Shipping Service on the "
                    "Synapse connection)."
                ) % self.name
            )
        try:
            connection.enqueue_update_order(
                self, shipping_service=shipping_service, carrier_code=carrier_code,
            )
        except UserError:
            # Already a user-facing message — let it surface as-is.
            raise
        except Exception as exc:
            _logger.exception("Synapse update failed for order %s", self.name)
            raise UserError(
                _(
                    "Could not update order %(order)s in Synapse:\n%(error)s"
                ) % {"order": self.name, "error": exc}
            )
        return self._notify_queued(_("Update queued for %s.") % self.name)

    def action_resync_synapse(self):
        """Replace the order in Synapse with its current contents: cancels the
        existing Synapse order and creates a new one (new Synapse order id).
        This is the only way to push line/quantity/address changes."""
        self.ensure_one()
        connection = self._synapse_connection()
        shipping_service, carrier_code = self._synapse_carrier_codes()
        connection.env["synapse.queue.job"]._enqueue(
            connection,
            "resync",
            "export",
            name=_("Resync %s") % self.name,
            payload={
                "shipping_service": shipping_service,
                "carrier_code": carrier_code,
            },
            res_model="sale.order",
            res_id=self.id,
            synapse_ref=self.synapse_orderid,
        )
        return self._notify_queued(
            _("Resync (cancel + recreate) queued for %s.") % self.name
        )

    def action_cancel_synapse(self):
        self.ensure_one()
        connection = self._synapse_connection()
        connection.env["synapse.queue.job"]._enqueue(
            connection,
            "cancel",
            "export",
            name=_("Cancel %s") % self.name,
            res_model="sale.order",
            res_id=self.id,
            synapse_ref=self.synapse_orderid,
        )
        return self._notify_queued(_("Cancellation queued for %s.") % self.name)

    def action_refresh_synapse_status(self):
        """Pull this order's current status straight from Synapse (order-info)."""
        self.ensure_one()
        connection = self._synapse_connection()
        try:
            connection.refresh_order_status(self)
        except UserError:
            # Already a user-facing message — let it surface as-is.
            raise
        except Exception as exc:
            _logger.exception(
                "Synapse status refresh failed for order %s", self.name
            )
            raise UserError(
                _(
                    "Could not refresh the Synapse status for %(order)s:\n%(error)s"
                ) % {"order": self.name, "error": exc}
            )
        label = dict(self._fields["synapse_order_status"].selection).get(
            self.synapse_order_status, self.synapse_order_status or _("unknown")
        )
        return self._notify_queued(
            _("Synapse status for %(order)s: %(status)s")
            % {"order": self.name, "status": label}
        )

    def action_fetch_synapse_attachments(self):
        self.ensure_one()
        connection = self._synapse_connection()
        connection.env["synapse.queue.job"]._enqueue(
            connection,
            "attachment",
            "import",
            name=_("Attachments %s") % self.name,
            res_model="sale.order",
            res_id=self.id,
            synapse_ref=self.synapse_orderid,
        )
        return self._notify_queued(_("Attachment download queued for %s.") % self.name)

    def action_upload_synapse_attachments(self):
        """Queue an upload of this order's documents (shipping label, packing
        slip, warehouse docs) to Synapse."""
        self.ensure_one()
        connection = self._synapse_connection()
        connection.env["synapse.queue.job"]._enqueue(
            connection,
            "attachment",
            "export",
            name=_("Upload Attachments %s") % self.name,
            res_model="sale.order",
            res_id=self.id,
            synapse_ref=self.synapse_orderid,
        )
        return self._notify_queued(_("Attachment upload queued for %s.") % self.name)

    def action_upload_file_to_synapse(self):
        """Open a wizard to upload an arbitrary file (e.g. an image for testing)
        and send it to Synapse immediately."""
        self.ensure_one()
        if not self.synapse_orderid:
            raise UserError(
                _("Order %s has not been sent to Synapse yet; cannot upload "
                  "attachments.") % self.name
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Upload File to Synapse"),
            "res_model": "synapse.attachment.upload.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_sale_order_id": self.id,
                "default_connection_id": self._synapse_connection().id,
            },
        }

    # ------------------------------------------------------------------
    # Daily Synapse report (mail_template_synapse_daily_report, 5pm Pacific)
    # ------------------------------------------------------------------
    @api.model
    def _synapse_report_day_bounds(self):
        """Return (today_date, start_utc_str, end_utc_str): the calendar day
        "today" in SYNAPSE_REPORT_TZ, and its start/end converted to UTC
        strings for filtering create_date. Bucketing by a fixed timezone (not
        the cron user's tz, which is usually UTC) keeps "today" meaning the
        same thing regardless of server tz."""
        tz = pytz.timezone(SYNAPSE_REPORT_TZ)
        now_local = pytz.utc.localize(fields.Datetime.now()).astimezone(tz)
        today = now_local.date()
        start_local = tz.localize(datetime.combine(today, dt_time.min))
        end_local = tz.localize(datetime.combine(today, dt_time.max))
        to_utc_str = lambda dt: fields.Datetime.to_string(
            dt.astimezone(pytz.utc).replace(tzinfo=None)
        )
        return today, to_utc_str(start_local), to_utc_str(end_local)

    @api.model
    def cron_send_synapse_daily_report(self):
        """Cron: email the daily Synapse Anywhere orders report — orders
        shipped today, orders generated today, and open orders not yet
        shipped (aging backlog). Never raises: a report failure must not
        break other scheduled jobs."""
        try:
            today, start_utc, end_utc = self._synapse_report_day_bounds()
            shipped = self.search(
                [("synapse_shipped_date", "=", today)], order="name"
            )
            generated = self.search(
                [
                    ("synapse_connection_id", "!=", False),
                    ("create_date", ">=", start_utc),
                    ("create_date", "<=", end_utc),
                ],
                order="name",
            )
            aging = self.search(
                [
                    ("synapse_connection_id", "!=", False),
                    ("synapse_status", "not in", ["shipped", "cancelled"]),
                    ("create_date", "<", start_utc),
                ],
                order="create_date asc",
            )
            self._send_synapse_daily_report_email(shipped, generated, aging, today)
        except Exception:
            _logger.exception("Synapse daily report failed")

    def _synapse_report_shipped_rows(self, shipped):
        """Row dicts for the template's shipped-today table (QWeb t-out
        auto-escapes, so no manual html_escape is needed here)."""
        shipments = self.env["synapse.shipment"].search(
            [("sale_order_id", "in", shipped.ids)]
        )
        ship_by_order = {}
        for shipment in shipments:
            ship_by_order.setdefault(shipment.sale_order_id.id, []).append(shipment)
        rows = []
        for order in shipped:
            order_shipments = ship_by_order.get(order.id, [])
            carrier = ", ".join(
                sorted({s.carrier for s in order_shipments if s.carrier})
            ) or (order.carrier_id.name or "")
            tracking = ", ".join(
                sorted(
                    {
                        t
                        for s in order_shipments
                        for t in s.line_ids.mapped("tracking_number")
                        if t
                    }
                )
            )
            rows.append(
                {
                    "order": order.name,
                    "customer": order.partner_id.display_name,
                    "synapse_orderid": order.synapse_orderid or "",
                    "synapse_shipid": order.synapse_shipid or "",
                    "carrier": carrier,
                    "tracking": tracking,
                }
            )
        return rows

    def _synapse_report_generated_rows(self, generated):
        status_labels = dict(self._fields["synapse_status"].selection)
        rows = []
        for order in generated:
            rows.append(
                {
                    "order": order.name,
                    "customer": order.partner_id.display_name,
                    "order_date": fields.Date.to_string(order.date_order.date())
                    if order.date_order
                    else "",
                    "total": "%s %.2f" % (order.currency_id.symbol, order.amount_total),
                    "status": status_labels.get(order.synapse_status, order.synapse_status)
                    or _("To Send"),
                }
            )
        return rows

    def _synapse_report_aging_rows(self, aging, today):
        status_labels = dict(self._fields["synapse_status"].selection)
        wms_labels = dict(SYNAPSE_ORDER_STATUSES)
        rows = []
        for order in aging[:SYNAPSE_REPORT_MAX_AGING_ROWS]:
            base_date = order.date_order or order.create_date
            days_open = (today - base_date.date()).days if base_date else ""
            rows.append(
                {
                    "order": order.name,
                    "customer": order.partner_id.display_name,
                    "order_date": fields.Date.to_string(base_date.date())
                    if base_date
                    else "",
                    "days_open": days_open,
                    "status": status_labels.get(order.synapse_status, order.synapse_status)
                    or _("To Send"),
                    "wms_status": wms_labels.get(
                        order.synapse_order_status, order.synapse_order_status or "—"
                    ),
                }
            )
        return rows

    def _send_synapse_daily_report_email(self, shipped, generated, aging, today):
        """Render and send the 'Synapse: Daily Orders Report' mail.template
        (bha_synapse.mail_template_synapse_daily_report) — never raises,
        matching the rest of the module's notification code. The recipient
        lives on the template itself (email_to), editable from Settings >
        Technical > Email Templates."""
        template = self.env.ref(
            "bha_synapse.mail_template_synapse_daily_report", raise_if_not_found=False
        )
        if not template:
            _logger.error("Synapse daily report template not found")
            return
        date_str = fields.Date.to_string(today)
        aging_rows = self._synapse_report_aging_rows(aging, today)
        aging_note = ""
        if len(aging) > SYNAPSE_REPORT_MAX_AGING_ROWS:
            aging_note = _("Showing the oldest %(shown)s of %(total)s open orders.") % {
                "shown": SYNAPSE_REPORT_MAX_AGING_ROWS,
                "total": len(aging),
            }
        try:
            template.with_context(
                report_date=date_str,
                shipped_title=_("Shipped Today (%s)") % len(shipped),
                shipped_rows=self._synapse_report_shipped_rows(shipped),
                generated_title=_("Orders Generated Today (%s)") % len(generated),
                generated_rows=self._synapse_report_generated_rows(generated),
                aging_title=_("Open Orders Not Yet Shipped (%s)") % len(aging),
                aging_rows=aging_rows,
                aging_note=aging_note,
            ).send_mail(
                self.env.company.id,
                force_send=True,
                raise_exception=False,
            )
        except Exception:
            _logger.exception("Could not send the Synapse daily report email")

    # ------------------------------------------------------------------
    # Freeport Daily Ready Orders Report (11:00 AM Pacific)
    # ------------------------------------------------------------------
    @api.model
    def cron_send_freeport_ready_orders_report(self):
        """Cron: send daily report at 11:00 AM with itemized table of orders that
        changed to 'Ready' from successful availability checks in the past 24 hours.
        Sent to freeport@baghouseamerica.com and m.andazola@freeport-logistics.com.
        Never raises."""
        try:
            end_utc = fields.Datetime.now()
            start_utc = end_utc - timedelta(hours=24)
            rows = self._get_freeport_ready_report_rows(start_utc, end_utc)
            self._send_freeport_ready_report_email(rows)
        except Exception:
            _logger.exception("Freeport daily ready orders report failed")

    @api.model
    def _get_freeport_ready_report_rows(self, start_utc, end_utc):
        """Build itemized rows (so, customer, item, qty) for outgoing pickings that
        became 'Ready' (assigned) within [start_utc, end_utc]."""
        start_utc_str = fields.Datetime.to_string(start_utc)
        end_utc_str = fields.Datetime.to_string(end_utc)

        # 1) Pickings whose state transitioned to 'assigned' via tracking values in window
        tracking_picking_ids = self.env["mail.tracking.value"].search([
            ("mail_message_id.model", "=", "stock.picking"),
            ("mail_message_id.date", ">=", start_utc_str),
            ("mail_message_id.date", "<=", end_utc_str),
            ("field_id.name", "=", "state"),
            ("new_value_char", "in", ["Ready", "assigned"]),
        ]).mapped("mail_message_id.res_id")

        # 2) Pickings currently in 'assigned' whose write_date is in window
        assigned_pickings = self.env["stock.picking"].search([
            ("picking_type_code", "=", "outgoing"),
            ("state", "=", "assigned"),
            ("write_date", ">=", start_utc_str),
            ("write_date", "<=", end_utc_str),
        ]).ids

        all_picking_ids = list(set(tracking_picking_ids) | set(assigned_pickings))
        if not all_picking_ids:
            return []

        pickings = self.env["stock.picking"].browse(all_picking_ids).filtered(
            lambda p: p.picking_type_code == "outgoing" and (p.sale_id or p.group_id.sale_id)
        )

        rows = []
        for picking in pickings:
            order = picking.sale_id or picking.group_id.sale_id
            customer_name = order.partner_id.name or ""
            for move in picking.move_ids.filtered(lambda m: m.state not in ("cancel", "draft")):
                qty = move.quantity if move.quantity else move.product_uom_qty
                if qty <= 0 and picking.state != "assigned":
                    continue
                display_qty = "%g" % qty if isinstance(qty, float) and qty.is_integer() else ("%.2f" % qty)
                rows.append({
                    "so": order.name,
                    "customer": customer_name,
                    "item": move.product_id.default_code or move.product_id.name or "",
                    "qty": display_qty,
                })

        rows.sort(key=lambda r: (r["so"], r["item"]))
        return rows

    def _send_freeport_ready_report_email(self, rows):
        """Send the Freeport Ready Orders report email using mail_template_freeport_ready_orders_report."""
        template = self.env.ref(
            "bha_synapse.mail_template_freeport_ready_orders_report",
            raise_if_not_found=False,
        )
        if not template:
            _logger.error("Freeport ready orders report template not found")
            return
        report_date = fields.Date.to_string(fields.Date.context_today(self))
        try:
            template.with_context(
                report_date=report_date,
                rows=rows,
            ).send_mail(
                self.env.company.id,
                force_send=True,
                raise_exception=False,
            )
        except Exception:
            _logger.exception("Could not send Freeport ready orders report email")


