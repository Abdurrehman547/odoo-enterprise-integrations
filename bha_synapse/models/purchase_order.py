# -*- coding: utf-8 -*-
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

from odoo.addons.bha_synapse.models.synapse_connection import SYNAPSE_ORDER_STATUSES


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    # Inbound (warehouse-import) mirror of the sale.order Synapse fields.
    # NOTE: synapse_status is deliberately its own selection — the PO reaches
    # 'received' (inbound terminal) instead of the SO's 'shipped', so the two
    # must not share a constant.
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
            ("received", "Received"),
            ("cancelled", "Cancelled"),
        ],
        string="Synapse Status",
        copy=False,
        help="High-level Odoo-side tracking of the inbound order's Synapse "
        "lifecycle (warehouse imports).",
    )
    synapse_order_status = fields.Selection(
        SYNAPSE_ORDER_STATUSES,
        string="Synapse Order Status",
        copy=False,
        readonly=True,
        help="The granular order status reported by Synapse itself "
        "(order-info), e.g. Hold / Released / Arrived / Received. Use "
        "'Refresh Synapse Status' to update it.",
    )
    synapse_to_send = fields.Boolean(
        string="Send to Synapse",
        copy=False,
        help="When set, the scheduled job will push this purchase order to "
        "Synapse as an inbound order.",
    )
    synapse_received_date = fields.Date(
        string="Synapse Received Date",
        copy=False,
        readonly=True,
        help="Date this inbound order first turned 'Arrived'/'Received' in "
        "Synapse. Stamped once by 'Refresh Synapse Status'.",
    )

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

    def action_send_to_synapse(self):
        """Open the wizard to review and push this purchase order to Synapse
        as an inbound order (warehouse import). The wizard derives the lines,
        order type and PO# from the PO itself and lets the user adjust them
        before sending."""
        self.ensure_one()
        if self.state not in ("purchase", "done"):
            raise UserError(
                _(
                    "Purchase order %(order)s must be confirmed before it can "
                    "be sent to Synapse."
                ) % {"order": self.name}
            )
        if self.synapse_orderid:
            raise UserError(
                _(
                    "Purchase order %(order)s has already been sent to Synapse "
                    "(order id %(sid)s). Use 'Resync Order (Replace)' to send "
                    "it again with its current contents."
                ) % {"order": self.name, "sid": self.synapse_orderid}
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Send to Synapse (Inbound)"),
            "res_model": "synapse.purchase.order.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_purchase_order_id": self.id},
        }

    def action_resync_synapse(self):
        """Replace the inbound order in Synapse with this PO's current
        contents: cancels the existing Synapse order and creates a new one
        (new Synapse order id)."""
        self.ensure_one()
        connection = self._synapse_connection()
        connection.env["synapse.queue.job"]._enqueue(
            connection,
            "purchase_resync",
            "export",
            name=_("Resync Purchase Order %s") % self.name,
            res_model="purchase.order",
            res_id=self.id,
            synapse_ref=self.synapse_orderid,
        )
        return self._notify_queued(
            _("Resync (cancel + recreate) queued for %s.") % self.name
        )

    def action_cancel_synapse(self):
        """Queue a cancellation of this PO's inbound order in Synapse."""
        self.ensure_one()
        connection = self._synapse_connection()
        connection.env["synapse.queue.job"]._enqueue(
            connection,
            "purchase_cancel",
            "export",
            name=_("Cancel Purchase Order %s") % self.name,
            res_model="purchase.order",
            res_id=self.id,
            synapse_ref=self.synapse_orderid,
        )
        return self._notify_queued(_("Cancellation queued for %s.") % self.name)

    def action_refresh_synapse_status(self):
        """Pull this inbound order's current status straight from Synapse
        (order-info)."""
        self.ensure_one()
        connection = self._synapse_connection()
        try:
            connection.refresh_order_status(self)
        except UserError:
            # Already a user-facing message — let it surface as-is.
            raise
        except Exception as exc:
            _logger.exception(
                "Synapse status refresh failed for purchase order %s", self.name
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
