# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.addons.bha_synapse.models.synapse_connection import SYNAPSE_ORDER_STATUSES

_logger = logging.getLogger(__name__)


class StockQuant(models.Model):
    _inherit = "stock.quant"

    def action_pull_synapse_inventory(self):
        """Refresh the Synapse inventory snapshot for the related product."""
        connection = self.env["synapse.connection"]._get_default_connection()
        for quant in self:
            if quant.product_id.default_code:
                connection.pull_inventory(item=quant.product_id.default_code)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "sticky": False,
                "message": _("Synapse inventory refreshed."),
            },
        }


class StockPicking(models.Model):
    _inherit = "stock.picking"

    synapse_connection_id = fields.Many2one(
        "synapse.connection", string="Synapse Connection", copy=False
    )
    synapse_orderid = fields.Char(string="Synapse Order ID", copy=False, readonly=True, index=True)
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
        help="High-level Odoo-side tracking of the delivery's Synapse lifecycle.",
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
        help="When set, the scheduled job will push this delivery to Synapse.",
    )
    synapse_shipped_date = fields.Date(
        string="Synapse Shipped Date",
        copy=False,
        readonly=True,
        help="Date this delivery was marked shipped in Synapse.",
    )
    synapse_customer_packing_list = fields.Boolean(
        string="Customer Packing List",
        help="When set, the customer supplies their own packing list: no "
        "Packing Slip is generated or sent to Synapse for this delivery.",
    )
    synapse_shipment_ids = fields.One2many(
        "synapse.shipment", "picking_id", string="Synapse Shipments"
    )
    synapse_shipment_count = fields.Integer(
        compute="_compute_synapse_shipment_count", string="Shipments"
    )

    def _synapse_shipment_domain(self):
        """Shipments belonging to this delivery: stored link plus fallback on Synapse orderid or delivery number."""
        self.ensure_one()
        domain = [("picking_id", "=", self.id)]
        terms = [("picking_id", "=", self.id)]
        if self.synapse_orderid:
            terms.append(("orderid", "=", self.synapse_orderid))
        if self.name:
            terms.append(("po_number", "=", self.name))
        if len(terms) > 1:
            domain = ["|"] * (len(terms) - 1) + terms
        return domain

    @api.depends("synapse_orderid")
    def _compute_synapse_shipment_count(self):
        Shipment = self.env["synapse.shipment"]
        for picking in self:
            picking.synapse_shipment_count = Shipment.search_count(
                picking._synapse_shipment_domain()
            )

    def action_view_synapse_shipments(self):
        """Smart button: open the Synapse shipments of this delivery."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Synapse Shipments"),
            "res_model": "synapse.shipment",
            "view_mode": "list,form",
            "domain": self._synapse_shipment_domain(),
            "target": "current",
        }

    def _synapse_connection(self):
        return (
            self.synapse_connection_id
            or (self.sale_id and self.sale_id.synapse_connection_id)
            or self.env["synapse.connection"]._get_default_connection()
        )

    def _notify_queued(self, message):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"type": "success", "sticky": False, "message": message},
        }

    def _synapse_carrier_codes(self):
        """Return (shipping_service, carrier_code) derived from the delivery's
        carrier, or the related sales order's carrier."""
        carrier = self.carrier_id or (self.sale_id and getattr(self.sale_id, "carrier_id", False))
        if not carrier:
            return None, None
        shipping_service = carrier.synapse_service_code or carrier.name
        return shipping_service or None, carrier.synapse_carrier_code or None

    def action_send_to_synapse(self):
        """Open the wizard to review and push this delivery to Synapse."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Send to Synapse"),
            "res_model": "synapse.order.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_picking_id": self.id,
                "default_sale_order_id": self.sale_id.id if self.sale_id else False,
            },
        }

    def action_update_synapse(self):
        """Push header-level changes (carrier / shipping / routing) of an
        already-sent delivery to Synapse via update-order."""
        self.ensure_one()
        if not self.synapse_orderid:
            raise UserError(
                _("Delivery %s has not been sent to Synapse yet; nothing to update.")
                % self.name
            )
        connection = self._synapse_connection()
        shipping_service, carrier_code = self._synapse_carrier_codes()
        if not shipping_service and not carrier_code and not (
            connection.default_shipping_service
        ):
            raise UserError(
                _(
                    "Delivery %s has no Delivery Method, so there is no carrier or "
                    "shipping service to update in Synapse. Set a Delivery Method "
                    "on the delivery first (or a Default Shipping Service on the "
                    "Synapse connection)."
                ) % self.name
            )
        try:
            connection.enqueue_update_order(
                self, shipping_service=shipping_service, carrier_code=carrier_code,
            )
        except UserError:
            raise
        except Exception as exc:
            _logger.exception("Synapse update failed for delivery %s", self.name)
            raise UserError(
                _(
                    "Could not update delivery %(delivery)s in Synapse:\n%(error)s"
                ) % {"delivery": self.name, "error": exc}
            )
        return self._notify_queued(_("Update queued for %s.") % self.name)

    def action_resync_synapse(self):
        """Replace the delivery order in Synapse with its current contents: cancels the
        existing Synapse order and creates a new one (new Synapse order id)."""
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
            res_model="stock.picking",
            res_id=self.id,
            synapse_ref=self.synapse_orderid or self.name,
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
            res_model="stock.picking",
            res_id=self.id,
            synapse_ref=self.synapse_orderid or self.name,
        )
        return self._notify_queued(_("Cancellation queued for %s.") % self.name)

    def action_refresh_synapse_status(self):
        """Pull this delivery's current status straight from Synapse (order-info)."""
        self.ensure_one()
        connection = self._synapse_connection()
        try:
            connection.refresh_order_status(self)
        except UserError:
            raise
        except Exception as exc:
            _logger.exception(
                "Synapse status refresh failed for delivery %s", self.name
            )
            raise UserError(
                _(
                    "Could not refresh the Synapse status for %(delivery)s:\n%(error)s"
                ) % {"delivery": self.name, "error": exc}
            )
        label = dict(self._fields["synapse_order_status"].selection).get(
            self.synapse_order_status, self.synapse_order_status or _("unknown")
        )
        return self._notify_queued(
            _("Synapse status for %(delivery)s: %(status)s")
            % {"delivery": self.name, "status": label}
        )

    def action_fetch_synapse_attachments(self):
        self.ensure_one()
        connection = self._synapse_connection()
        connection.env["synapse.queue.job"]._enqueue(
            connection,
            "attachment",
            "import",
            name=_("Attachments %s") % self.name,
            res_model="stock.picking",
            res_id=self.id,
            synapse_ref=self.synapse_orderid,
        )
        return self._notify_queued(_("Attachment download queued for %s.") % self.name)

    def action_upload_synapse_attachments(self):
        """Queue an upload of this delivery's documents (shipping label, packing slip) to Synapse."""
        self.ensure_one()
        connection = self._synapse_connection()
        connection.env["synapse.queue.job"]._enqueue(
            connection,
            "attachment",
            "export",
            name=_("Upload Attachments %s") % self.name,
            res_model="stock.picking",
            res_id=self.id,
            synapse_ref=self.synapse_orderid,
        )
        return self._notify_queued(_("Attachment upload queued for %s.") % self.name)

    def action_upload_file_to_synapse(self):
        """Open a wizard to upload an arbitrary file and send it to Synapse immediately."""
        self.ensure_one()
        if not self.synapse_orderid:
            raise UserError(
                _("Delivery %s has not been sent to Synapse yet; cannot upload attachments.") % self.name
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Upload File to Synapse"),
            "res_model": "synapse.attachment.upload.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_picking_id": self.id,
                "default_sale_order_id": self.sale_id.id if self.sale_id else False,
                "default_connection_id": self._synapse_connection().id,
            },
        }
