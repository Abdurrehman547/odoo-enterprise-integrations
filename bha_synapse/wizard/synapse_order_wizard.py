# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.bha_synapse.models.synapse_connection import (
    SYNAPSE_ORDER_PRIORITIES,
    SYNAPSE_ORDER_TYPES,
)


class SynapseOrderWizard(models.TransientModel):
    _name = "synapse.order.wizard"
    _description = "Send Order to Synapse"

    picking_id = fields.Many2one(
        "stock.picking", string="Delivery Order", readonly=True
    )
    sale_order_id = fields.Many2one(
        "sale.order", string="Sale Order", required=False, readonly=True
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
        SYNAPSE_ORDER_TYPES,
        string="Order Type",
        help="Synapse order_type. Use O (Outbound) for a normal sales-order "
        "shipment. Outbound types (O/V/T/U/F) leave the facility (need From "
        "Facility); inbound/receipt types (R/A/C/P/Q/I) receive into it (need "
        "To Facility). V is ReturnVendor, not an outbound shipment.",
    )
    priority = fields.Selection(
        SYNAPSE_ORDER_PRIORITIES,
        string="Priority",
        help="Synapse order priority sent on create-order (header 'priority'): "
        "e.g. 0=Hot, A=Normal, S=SameDayShip.",
    )
    shipping_carrier_id = fields.Many2one(
        "delivery.carrier",
        string="Shipping Method",
        help="Odoo delivery method for this order. Its name is sent to Synapse "
        "as the order's shipping service / carrier.",
    )
    po_number = fields.Char(string="PO Number")
    shipping_term = fields.Char(
        string="Shipping Terms",
        readonly=True,
        help="Incoterms / shipping terms pulled from the sales order.",
    )
    reference = fields.Char(string="Reference")
    line_ids = fields.One2many(
        "synapse.order.wizard.line", "wizard_id", string="Lines"
    )

    @api.onchange("connection_id")
    def _onchange_connection_id(self):
        if self.connection_id and not self.order_type:
            self.order_type = self.connection_id.default_order_type
        if self.connection_id and not self.priority:
            # Connection default if configured, else Normal ('A').
            self.priority = self.connection_id.default_priority or "A"
        if not self.shipping_carrier_id:
            carrier = (self.picking_id and self.picking_id.carrier_id) or getattr(
                self.sale_order_id, "carrier_id", False
            )
            if carrier:
                self.shipping_carrier_id = carrier

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        # Driven by the Delivery Order: derive the sales order from the picking.
        # Falls back to a directly-supplied sale_order_id for backward compat.
        picking = self.env["stock.picking"].browse(res.get("picking_id"))
        order = picking.sale_id if picking else self.env["sale.order"].browse(
            res.get("sale_order_id")
        )
        if picking:
            res.setdefault("picking_id", picking.id)
            if order:
                res.setdefault("sale_order_id", order.id)
            connection = (
                picking.synapse_connection_id
                or (order and order.synapse_connection_id)
                or self.env["synapse.connection"].search([("active", "=", True)], limit=1)
            )
            # "When orders are sent to Synapse the Reference should be the "Source Document"
            # from the delivery on Odoo and the "PO" on Synapse should be the delivery number (WH/OUT/33678) for example"
            res.setdefault("reference", picking.origin or (order and order.name) or "")
            res.setdefault("po_number", picking.name)

            shipping_term = False
            if order:
                shipping_term = getattr(order, "ship_term", False)
                if not shipping_term:
                    term = getattr(order, "shipping_term", False)
                    shipping_term = term.name if term else False
            if shipping_term:
                res.setdefault("shipping_term", shipping_term)

            if connection:
                res.setdefault("order_type", connection.default_order_type)
                res["priority"] = connection.default_priority or "A"
            else:
                res.setdefault("priority", "A")

            carrier = picking.carrier_id or (order and getattr(order, "carrier_id", False))
            if carrier:
                res.setdefault("shipping_carrier_id", carrier.id)

            lines = []
            for move in picking.move_ids.filtered(lambda m: m.state not in ("cancel", "draft")):
                if move.product_id and move.product_id.type != "service":
                    qty = move.product_uom_qty or move.quantity
                    lines.append(
                        (
                            0,
                            0,
                            {
                                "product_id": move.product_id.id,
                                "quantity": qty,
                                "uom_code": connection.default_uom if connection else "EA",
                            },
                        )
                    )
            res["line_ids"] = lines
            return res

        elif order:
            res.setdefault("sale_order_id", order.id)
            connection = order.synapse_connection_id or self.env["synapse.connection"].browse(
                res.get("connection_id")
            )
            res.setdefault("reference", order.name)
            res.setdefault(
                "po_number",
                getattr(order, "po_no", False)
                or order.client_order_ref
                or order.name,
            )
            shipping_term = getattr(order, "ship_term", False)
            if not shipping_term:
                term = getattr(order, "shipping_term", False)
                shipping_term = term.name if term else False
            if shipping_term:
                res.setdefault("shipping_term", shipping_term)
            if connection:
                res.setdefault("order_type", connection.default_order_type)
                res["priority"] = connection.default_priority or "A"
            else:
                res.setdefault("priority", "A")
            carrier = getattr(order, "carrier_id", False)
            if carrier:
                res.setdefault("shipping_carrier_id", carrier.id)
            Connection = self.env["synapse.connection"]
            res["line_ids"] = [
                (
                    0,
                    0,
                    {
                        "product_id": line.product_id.id,
                        "quantity": line.product_uom_qty,
                        "uom_code": connection.default_uom if connection else "EA",
                    },
                )
                for line in order.order_line
                if Connection._is_pickable_line(line)
            ]
        return res

    def action_send(self):
        self.ensure_one()
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
        target = self.picking_id or self.sale_order_id
        if not target:
            raise UserError(_("No delivery order or sale order to send."))

        target.write(
            {
                "synapse_connection_id": self.connection_id.id,
                "synapse_to_send": True,
                "synapse_status": "draft",
            }
        )
        if self.picking_id and self.sale_order_id:
            self.sale_order_id.write({"synapse_connection_id": self.connection_id.id})

        carrier = self.shipping_carrier_id
        shipping_service = False
        carrier_code = False
        if carrier:
            shipping_service = carrier.synapse_service_code or carrier.name
            carrier_code = carrier.synapse_carrier_code

        job = self.connection_id.enqueue_order(
            target,
            order_type=self.order_type,
            line_values=line_values,
            shipping_service=shipping_service or None,
            carrier_code=carrier_code or None,
            priority=self.priority or None,
            po_number=self.po_number,
            reference=self.reference,
        )
        job.process_now()
        if job.state == "failed":
            raise UserError(
                _("%(target)s could not be sent to Synapse:\n%(reason)s")
                % {
                    "target": target.name,
                    "reason": job.message or _("Unknown error"),
                }
            )
        if job.state == "done":
            message = _("%s sent to Synapse.") % target.name
        else:
            message = _(
                "%s queued for Synapse — the sync scheduler will send it "
                "within the next few minutes."
            ) % target.name
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"type": "success", "sticky": False, "message": message},
        }


class SynapseOrderWizardLine(models.TransientModel):
    _name = "synapse.order.wizard.line"
    _description = "Send Order to Synapse Line"

    wizard_id = fields.Many2one(
        "synapse.order.wizard", string="Wizard", required=True, ondelete="cascade"
    )
    product_id = fields.Many2one("product.product", string="Product", required=True)
    item_code = fields.Char(
        string="Synapse Item", related="product_id.default_code", readonly=True
    )
    quantity = fields.Float(string="Quantity", required=True)
    uom_code = fields.Char(string="UoM", default="EA")
