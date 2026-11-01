from odoo import models, fields


class DeliveryCarrier(models.Model):
    _inherit = "delivery.carrier"

    x_duoplane_margin = fields.Float(string="Duoplane Margin (%)")
    x_duoplane_fixed_margin = fields.Float(string="Duoplane Additional Margin")
    x_duoplane_free_over = fields.Boolean(string="Duoplane Free if order amount is above")
    x_duoplane_free_over_amount = fields.Float(string="Duoplane Free Shipping Amount")

    def _is_duoplane_order(self, order):
        return bool(order and getattr(order, "x_duoplane_po_id", False))

    def _get_free_threshold_field_name(self):
        """
        Odoo uses a boolean `free_over` plus a float threshold.
        That float is commonly `amount`. Some setups name it `free_over_amount`.
        We'll detect safely.
        """
        if "amount" in self._fields:
            return "amount"
        if "free_over_amount" in self._fields:
            return "free_over_amount"
        return None

    def rate_shipment(self, order):
        self.ensure_one()

        # 1. Get RAW provider price first
        method_name = f"{self.delivery_type}_rate_shipment"
        if hasattr(self, method_name):
            res = getattr(self, method_name)(order)
        else:
            res = super().rate_shipment(order)

        if not res or not res.get("success") or "price" not in res:
            return res

        base_price = res["price"]
        is_duoplane = self._is_duoplane_order(order)

        # 2. Choose which set of values to apply
        if is_duoplane:
            margin = self.x_duoplane_margin or 0.0
            fixed = self.x_duoplane_fixed_margin or 0.0
            free_enabled = bool(self.x_duoplane_free_over)
            free_amount = self.x_duoplane_free_over_amount or 0.0
        else:
            margin = self.margin or 0.0
            fixed = self.fixed_margin or 0.0
            free_enabled = bool(self.free_over)
            threshold_field = self._get_free_threshold_field_name()
            free_amount = getattr(self, threshold_field, 0.0) if threshold_field else 0.0

        # 3. Apply logic
        if free_enabled and free_amount and order.amount_total >= free_amount:
            final_price = 0.0
        else:
            final_price = (base_price * (1.0 + (margin / 100.0))) + fixed

        # 4. THE FIX: Satisfy the Wizard (carrier_price) AND the Order (price)
        res.update({
            "price": final_price,
            "carrier_price": final_price  # This stops the KeyError crash
        })

        return res