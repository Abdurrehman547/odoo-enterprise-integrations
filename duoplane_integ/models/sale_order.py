# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    x_duoplane_po_id = fields.Integer(
        string='Duoplane PO ID',
        readonly=True,
        copy=False,
        index=True,
        help='Duoplane Purchase Order ID'
    )
    x_retailer = fields.Char(
        string='Retailer',
        readonly=True,
        copy=False,
        help='Retailer name from Duoplane'
    )
    x_duoplane_synced = fields.Boolean(
        string='Synced from Duoplane',
        compute='_compute_duoplane_synced',
        store=True,
        help='Indicates if this order was imported from Duoplane'
    )
    x_duoplane_shipment_posted = fields.Boolean(
        string='Shipment Posted to Duoplane',
        default=False,
        copy=False
    )

    @api.depends('x_duoplane_po_id')
    def _compute_duoplane_synced(self):
        for order in self:
            order.x_duoplane_synced = bool(order.x_duoplane_po_id)

    def action_confirm(self):
        res = super().action_confirm()

        for order in self:
            # Only for Duoplane orders (change if you want all orders)
            if not order.x_duoplane_synced:
                continue

            # Optional: only your Texas company
            # if "TEXAS CENTRAL DISTRIBUTION" not in (order.company_id.name or "").upper():
            #     continue

            try:
                order._auto_apply_duoplane_shipping()
            except Exception as e:
                # do NOT block confirmation if rating fails
                _logger.exception("Auto Duoplane shipping failed for %s: %s", order.name, str(e))

        return res

    def _resolve_duoplane_carrier(self):
        """
        Determine which shipping method to apply on a Duoplane order:
          1. The carrier already set on the order (copied from the Duoplane
             configuration's selected Shipping Method at fetch time).
          2. The Shipping Method configured on the active Duoplane configuration.
          3. Fallback to a carrier named 'UPS US' (legacy behaviour).
        """
        self.ensure_one()

        if self.carrier_id:
            return self.carrier_id

        config = self.env['duoplane.config'].sudo().search([
            ('active', '=', True),
            ('delivery_carrier_id', '!=', False),
        ], limit=1)
        if config.delivery_carrier_id:
            return config.delivery_carrier_id

        return self.env["delivery.carrier"].search([
            ("name", "=", "UPS US"),
            "|", ("company_id", "=", self.company_id.id), ("company_id", "=", False)
        ], limit=1)

    def _auto_apply_duoplane_shipping(self):
        """
        Automates the UI wizard for the Duoplane-configured shipping method:
          action_open_delivery_wizard()
          choose carrier = configured Shipping Method
          set total_weight
          click Get rate (update_price)
          click Update (button_confirm/button_update)
          => delivery line added on SO
        """
        self.ensure_one()

        # Skip if delivery line already exists (prevents double lines)
        # In standard Odoo, delivery lines have is_delivery=True
        existing_delivery = self.order_line.filtered(lambda l: getattr(l, "is_delivery", False))
        if existing_delivery:
            # If you want to force recalculation even if delivery exists, remove this block
            return True

        # Ensure wizard model exists
        if "choose.delivery.carrier" not in self.env:
            raise UserError(_("Delivery wizard not found. Ensure modules 'delivery' and 'sale_delivery' are installed."))

        # Resolve the configured shipping method
        carrier = self._resolve_duoplane_carrier()
        if not carrier:
            raise UserError(_("No shipping method configured for Duoplane orders. "
                              "Please set a Shipping Method in the Duoplane configuration."))

        # Compute total weight similar to wizard expectation (exclude delivery lines)
        total_weight = 0.0
        for line in self.order_line.filtered(lambda l: not getattr(l, "is_delivery", False)):
            total_weight += (line.product_id.weight or 0.0) * line.product_uom_qty

        # Get wizard context like UI does
        action = self.action_open_delivery_wizard()
        wiz_ctx = dict(self.env.context, **(action.get("context") or {}))

        # Create wizard
        wiz = self.env["choose.delivery.carrier"].with_context(wiz_ctx).create({
            "order_id": self.id,
            "carrier_id": carrier.id,
        })

        # Set weight on wizard if field exists (it does in your debug tooltip)
        if "total_weight" in wiz._fields:
            wiz.total_weight = total_weight

        # Click "Get rate" (your tooltip shows method update_price)
        wiz.update_price()

        # Confirm/update wizard to actually add delivery line on SO
        if hasattr(wiz, "button_confirm"):
            wiz.button_confirm()
        elif hasattr(wiz, "button_update"):
            wiz.button_update()
        else:
            # Fallback: try common order method if wizard differs
            if hasattr(self, "set_delivery_line"):
                # display_price is "Cost" field in UI
                self.set_delivery_line(carrier, float(getattr(wiz, "display_price", 0.0) or 0.0))
            else:
                raise UserError(_("Could not apply delivery: wizard confirm method not found."))

        _logger.info(
            "Duoplane shipping applied on %s | carrier=%s | weight=%.3f | cost=%.2f",
            self.name, carrier.name, total_weight, float(getattr(wiz, "display_price", 0.0) or 0.0)
        )
        return True


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    x_duoplane_order_item_id = fields.Integer(
        string='Duoplane Order Item ID',
        help='The order_item_id from Duoplane, needed for shipment posting'
    )
