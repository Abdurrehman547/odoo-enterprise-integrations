# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.bha_synapse.models.synapse_connection import (
    SYNAPSE_INBOUND_ORDER_TYPES,
    SYNAPSE_ORDER_PRIORITIES,
)


class SynapsePurchaseOrderWizard(models.TransientModel):
    _name = "synapse.purchase.order.wizard"
    _description = "Send Purchase Order to Synapse (Inbound)"

    purchase_order_id = fields.Many2one(
        "purchase.order", string="Purchase Order", required=True, readonly=True
    )
    connection_id = fields.Many2one(
        "synapse.connection",
        string="Connection",
        required=True,
        default=lambda self: self.env["synapse.connection"].search(
            [("active", "=", True)], limit=1
        ),
    )
    order_type = fields.Selection(
        SYNAPSE_INBOUND_ORDER_TYPES,
        string="Order Type",
        help="Synapse order_type for this inbound (warehouse import). All of "
        "these receive INTO the Inbound To Facility (the AZ warehouse). R "
        "(Receipt) is the standard Synapse inbound type and the default; "
        "avoid 'I' unless FrPRT confirms it (Synapse treats it as outbound).",
    )
    priority = fields.Selection(
        SYNAPSE_ORDER_PRIORITIES,
        string="Priority",
        help="Synapse order priority sent on create-order (header 'priority'): "
        "e.g. 0=Hot, A=Normal.",
    )
    po_number = fields.Char(
        string="PO Number",
        help="Sent as the Synapse po_number. Defaults to the Vendor Reference, "
        "then the PO name. Changing it here writes it back to the PO's Vendor "
        "Reference.",
    )
    line_ids = fields.One2many(
        "synapse.purchase.order.wizard.line", "wizard_id", string="Lines"
    )

    @api.onchange("connection_id")
    def _onchange_connection_id(self):
        if self.connection_id and not self.order_type:
            self.order_type = (
                self.connection_id.default_inbound_order_type or "R"
            )
        if self.connection_id and not self.priority:
            # Connection default if configured, else Normal ('A').
            self.priority = self.connection_id.default_priority or "A"

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        order = self.env["purchase.order"].browse(res.get("purchase_order_id"))
        if order:
            res.setdefault("purchase_order_id", order.id)
            connection = self.env["synapse.connection"].browse(res.get("connection_id"))
            # PO# — prefer the vendor reference, then the PO name, so the
            # field is never blank.
            res.setdefault("po_number", order.partner_ref or order.name)
            if connection:
                res.setdefault(
                    "order_type",
                    connection.default_inbound_order_type or "R",
                )
                res["priority"] = connection.default_priority or "A"
            else:
                res.setdefault("order_type", "R")
                res.setdefault("priority", "A")
            # Use the same receivable-line rule as the push so services and
            # section/note lines don't appear as items to receive.
            Connection = self.env["synapse.connection"]
            res["line_ids"] = [
                (
                    0,
                    0,
                    {
                        "product_id": line.product_id.id,
                        "quantity": line.product_qty,
                        "uom_code": connection.default_uom if connection else "EA",
                    },
                )
                for line in order.order_line
                if Connection._is_receivable_line(line)
            ]
        return res

    def action_send(self):
        self.ensure_one()
        po = self.purchase_order_id
        if po.synapse_orderid:
            raise UserError(
                _(
                    "Purchase order %(order)s has already been sent to Synapse "
                    "(order id %(sid)s). Use 'Resync Order (Replace)' to send "
                    "it again with its current contents."
                ) % {"order": po.name, "sid": po.synapse_orderid}
            )
        if not self.line_ids:
            raise UserError(_("There are no lines to send."))
        line_values = [
            {
                "product": line.product_id,
                "qty": line.quantity,
                "uom": line.uom_code,
            }
            for line in self.line_ids
        ]
        # Allow the wizard to override the Vendor Reference before pushing,
        # mirroring the sale-order wizard's PO# override.
        if self.po_number and self.po_number != (po.partner_ref or ""):
            po.partner_ref = self.po_number
        po.write(
            {
                "synapse_connection_id": self.connection_id.id,
                "synapse_to_send": True,
                "synapse_status": "draft",
            }
        )
        job = self.connection_id.enqueue_inbound_order(
            po,
            order_type=self.order_type,
            line_values=line_values,
            priority=self.priority or None,
        )
        # Don't make the user wait up to 10 minutes for the queue cron: try to
        # push it to Synapse right now. process() already commits state as it
        # goes and never raises (job failures are caught and stored on the
        # job), so this is safe even if the live API call fails.
        job.process_now()
        if job.state == "failed":
            raise UserError(
                _("%(order)s could not be sent to Synapse:\n%(reason)s")
                % {
                    "order": po.name,
                    "reason": job.message or _("Unknown error"),
                }
            )
        if job.state == "done":
            message = _(
                "%s sent to Synapse as an inbound order."
            ) % po.name
        else:
            message = _(
                "%s queued for Synapse — the sync scheduler will send it "
                "within the next few minutes."
            ) % po.name
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"type": "success", "sticky": False, "message": message},
        }


class SynapsePurchaseOrderWizardLine(models.TransientModel):
    _name = "synapse.purchase.order.wizard.line"
    _description = "Send Purchase Order to Synapse Line"

    wizard_id = fields.Many2one(
        "synapse.purchase.order.wizard",
        string="Wizard",
        required=True,
        ondelete="cascade",
    )
    product_id = fields.Many2one("product.product", string="Product", required=True)
    item_code = fields.Char(
        string="Synapse Item", related="product_id.default_code", readonly=True
    )
    quantity = fields.Float(string="Quantity", required=True)
    uom_code = fields.Char(string="UoM", default="EA")
