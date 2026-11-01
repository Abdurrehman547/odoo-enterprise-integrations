# -*- coding: utf-8 -*-
import logging
from datetime import datetime

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .synapse_connection import SYNAPSE_ORDER_STATUSES

_logger = logging.getLogger(__name__)


class SynapseShipment(models.Model):
    _name = "synapse.shipment"
    _description = "Synapse Shipment"
    _order = "date_shipped desc, orderid desc"
    _rec_name = "orderid"

    connection_id = fields.Many2one(
        "synapse.connection", string="Connection", required=True, ondelete="cascade"
    )
    orderid = fields.Char(string="Order ID", index=True, required=True)
    shipid = fields.Char(string="Ship ID")
    facility = fields.Char(string="Facility")
    custid = fields.Char(string="Customer ID")
    po_number = fields.Char(string="PO Number", index=True)
    reference = fields.Char(string="Reference", index=True)
    order_status = fields.Char(
        string="Status Code",
        help="Raw Synapse order status code as reported by the WMS "
        "(normalized, e.g. '9' — see the Order Status label).",
    )
    order_status_label = fields.Char(
        string="Order Status",
        compute="_compute_order_status_label",
        help="Human-readable Synapse order status (e.g. 9 = Shipped). Falls "
        "back to the raw code when no mapping is known.",
    )
    date_shipped = fields.Datetime(string="Date Shipped")
    carrier = fields.Char(string="Carrier")
    scac = fields.Char(string="SCAC")
    prono = fields.Char(string="PRO Number")
    delivery_service = fields.Char(string="Delivery Service")
    qty_ship = fields.Float(string="Qty Shipped")
    weight_ship = fields.Float(string="Weight Shipped")
    shipping_cost = fields.Float(string="Shipping Cost")

    picking_id = fields.Many2one("stock.picking", string="Delivery Order")
    sale_order_id = fields.Many2one("sale.order", string="Sale Order")
    line_ids = fields.One2many(
        "synapse.shipment.line", "shipment_id", string="Packages"
    )
    last_sync = fields.Datetime(string="Last Sync", readonly=True)

    _sql_constraints = [
        (
            "orderid_shipid_conn_uniq",
            "unique(connection_id, orderid, shipid)",
            "A shipment with this order/ship id already exists for the connection.",
        )
    ]

    @staticmethod
    def _parse_dt(value):
        """Parse a Synapse date/datetime string (mm/dd/yyyy ...) to a datetime."""
        if not value:
            return False
        for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return False

    @staticmethod
    def _normalize_order_status(value):
        """Normalize a Synapse order status code.

        Some endpoints report zero-padded numeric codes ('09' for Shipped)
        while SYNAPSE_ORDER_STATUSES keys are unpadded ('9').
        """
        if value is None:
            return False
        code = str(value).strip()
        if len(code) > 1 and code.startswith("0") and code[1:].isdigit():
            code = code.lstrip("0")
        return code or False

    @api.depends("order_status")
    def _compute_order_status_label(self):
        labels = dict(SYNAPSE_ORDER_STATUSES)
        for shipment in self:
            code = shipment.order_status
            shipment.order_status_label = labels.get(code, code) or False

    def _match_picking(self, row):
        orderid = row.get("orderid")
        if orderid:
            picking = self.env["stock.picking"].search(
                [("synapse_orderid", "=", str(orderid))], limit=1
            )
            if picking:
                return picking
        # "PO" on Synapse should be the delivery number (WH/OUT/33678)
        po_num = (row.get("po_number") or "").strip()
        if po_num:
            picking = self.env["stock.picking"].search([("name", "=", po_num)], limit=1)
            if picking:
                return picking
        ref = (row.get("reference") or "").strip()
        if ref:
            picking = self.env["stock.picking"].search([("name", "=", ref)], limit=1)
            if picking:
                return picking
        return self.env["stock.picking"]

    def _upsert_from_synapse(self, row, connection):
        """Create or update a shipment (and its package lines) from a row."""
        orderid = str(row.get("orderid")) if row.get("orderid") is not None else False
        shipid = str(row.get("shipid")) if row.get("shipid") is not None else False
        if not orderid:
            return self.browse()
        picking = self._match_picking(row)
        sale_order = picking.sale_id if picking and picking.sale_id else self._match_sale_order(row)
        vals = {
            "connection_id": connection.id,
            "orderid": orderid,
            "shipid": shipid,
            "facility": row.get("facility"),
            "custid": row.get("custid"),
            "po_number": row.get("po_number"),
            "reference": row.get("reference"),
            "order_status": self._normalize_order_status(row.get("order_status")),
            "date_shipped": self._parse_dt(row.get("date_shipped")),
            "carrier": row.get("carrier"),
            "scac": row.get("scac"),
            "prono": row.get("prono"),
            "delivery_service": row.get("delivery_service"),
            "qty_ship": row.get("qty_ship") or 0.0,
            "weight_ship": row.get("weight_ship") or 0.0,
            "shipping_cost": row.get("shipping_cost") or 0.0,
            "picking_id": picking.id if picking else False,
            "sale_order_id": sale_order.id if sale_order else False,
            "last_sync": fields.Datetime.now(),
        }
        shipment = self.search(
            [
                ("connection_id", "=", connection.id),
                ("orderid", "=", orderid),
                ("shipid", "=", shipid),
            ],
            limit=1,
        )
        if shipment:
            shipment.write(vals)
        else:
            shipment = self.create(vals)

        shipment._sync_lines(row.get("plate_details") or [])
        if picking:
            shipment._write_back_to_picking(picking)
        elif sale_order:
            shipment._write_back_to_order(sale_order)
        return shipment

    def action_view_picking(self):
        """Smart button: open the delivery order this shipment belongs to."""
        self.ensure_one()
        picking = self._resolve_picking()
        if not picking:
            raise UserError(
                _(
                    "No delivery order in Odoo matches this shipment "
                    "(Synapse order %s, reference %s, PO %s)."
                )
                % (self.orderid, self.reference or "-", self.po_number or "-")
            )
        return {
            "type": "ir.actions.act_window",
            "res_model": "stock.picking",
            "view_mode": "form",
            "res_id": picking.id,
            "target": "current",
        }

    def _resolve_picking(self):
        """The linked delivery order: the stored link when present, else a live
        match which is persisted."""
        self.ensure_one()
        if self.picking_id:
            return self.picking_id
        picking = self._match_picking(
            {
                "orderid": self.orderid,
                "reference": self.reference,
                "po_number": self.po_number,
            }
        )
        if picking:
            self.picking_id = picking
        return picking

    def action_view_sale_order(self):
        """Smart button: open the sale order this shipment belongs to."""
        self.ensure_one()
        order = self._resolve_sale_order()
        if not order:
            raise UserError(
                _(
                    "No sale order in Odoo matches this shipment "
                    "(Synapse order %s, reference %s)."
                )
                % (self.orderid, self.reference or self.po_number or "-")
            )
        return {
            "type": "ir.actions.act_window",
            "res_model": "sale.order",
            "view_mode": "form",
            "res_id": order.id,
            "target": "current",
        }

    def _resolve_sale_order(self):
        """The linked sale order: the stored link when present, else a live
        match (Synapse order id, then reference / PO number) which is
        persisted so lists and reports can use it."""
        self.ensure_one()
        if self.sale_order_id:
            return self.sale_order_id
        order = self._match_sale_order(
            {
                "orderid": self.orderid,
                "reference": self.reference,
                "po_number": self.po_number,
            }
        )
        if order:
            self.sale_order_id = order
        return order

    def _match_sale_order(self, row):
        # The Synapse order id is the strongest link: it is stamped on the
        # sale order when the order is pushed to the WMS.
        orderid = row.get("orderid")
        if orderid:
            order = self.env["sale.order"].search(
                [("synapse_orderid", "=", str(orderid))], limit=1
            )
            if order:
                return order
        ref = row.get("reference") or row.get("po_number")
        if not ref:
            return self.env["sale.order"]
        order = self.env["sale.order"].search([("name", "=", ref)], limit=1)
        if not order:
            order = self.env["sale.order"].search(
                [("client_order_ref", "=", ref)], limit=1
            )
        return order

    def _sync_lines(self, plate_details):
        self.ensure_one()
        self.line_ids.unlink()
        Line = self.env["synapse.shipment.line"]
        for plate in plate_details:
            Line.create(
                {
                    "shipment_id": self.id,
                    "lpid": plate.get("lpid"),
                    "item": plate.get("item"),
                    "lot_number": plate.get("lot_number"),
                    "quantity": plate.get("quantity") or 0.0,
                    "uom": plate.get("uom"),
                    "weight": plate.get("weight") or 0.0,
                    "tracking_number": plate.get("tracking_number"),
                    "carton_type": plate.get("carton_type"),
                    "carton_sequence": plate.get("carton_sequence") or 0,
                }
            )
        return True

    def _write_back_to_picking(self, picking):
        """Flag the delivery as shipped and copy tracking onto it."""
        self.ensure_one()
        shipped_date = (
            self.date_shipped.date()
            if self.date_shipped
            else fields.Date.context_today(picking)
        )
        if picking.synapse_status != "shipped":
            picking.synapse_shipped_date = shipped_date
            picking.synapse_status = "shipped"

        if picking.sale_id and picking.sale_id.synapse_status != "shipped":
            picking.sale_id.synapse_shipped_date = shipped_date
            picking.sale_id.synapse_status = "shipped"

        tracking = ", ".join(
            self.line_ids.filtered("tracking_number").mapped("tracking_number")
        )
        if tracking:
            picking.carrier_tracking_ref = tracking

        if self.connection_id.auto_validate_delivery and picking.state not in ("done", "cancel"):
            self._auto_validate_delivery_picking(picking)

        return True

    def _auto_validate_delivery_picking(self, picking):
        """Update quantities based on Synapse shipment package lines, validate the delivery,
        and create backorders if unfulfilled quantities remain."""
        self.ensure_one()
        if not self.connection_id.auto_validate_delivery:
            return False

        shipped_by_item = {}
        for line in self.line_ids:
            item_code = (line.item or "").strip()
            if item_code:
                shipped_by_item[item_code] = shipped_by_item.get(item_code, 0.0) + (line.quantity or 0.0)

        total_qty_shipped = self.qty_ship or sum(shipped_by_item.values())

        if picking.state == "confirmed":
            picking.action_assign()

        try:
            moves = picking.move_ids.filtered(lambda m: m.state not in ("done", "cancel"))
            if not moves:
                return False

            for move in moves:
                item_code = (move.product_id.default_code or "").strip()
                if item_code and item_code in shipped_by_item:
                    ship_qty = shipped_by_item[item_code]
                    done_qty = min(move.product_uom_qty, ship_qty)
                    move.quantity = done_qty
                    move.picked = (done_qty > 0)
                    shipped_by_item[item_code] -= done_qty
                elif not shipped_by_item and len(moves) == 1 and total_qty_shipped > 0:
                    done_qty = min(move.product_uom_qty, total_qty_shipped)
                    move.quantity = done_qty
                    move.picked = (done_qty > 0)
                else:
                    move.quantity = 0.0
                    move.picked = False

            tracking = ", ".join(
                self.line_ids.filtered("tracking_number").mapped("tracking_number")
            )
            if tracking:
                picking.carrier_tracking_ref = tracking

            picking.with_context(
                skip_backorder=True,
                cancel_backorder=False,
                from_synapse_shipment_validation=True,
            ).button_validate()

            order = picking.sale_id
            if order:
                backorders = order.picking_ids.filtered(
                    lambda p: p.picking_type_code == "outgoing"
                    and p.state not in ("done", "cancel")
                    and p.id != picking.id
                )
                if backorders:
                    order.message_post(
                        body=_(
                            "Delivery %(picking)s was automatically validated based on Synapse shipment %(orderid)s. "
                            "Backorder %(backorder)s was automatically created for remaining unfulfilled quantities."
                        )
                        % {
                            "picking": picking.name,
                            "orderid": self.orderid,
                            "backorder": ", ".join(backorders.mapped("name")),
                        }
                    )
                else:
                    order.message_post(
                        body=_(
                            "Delivery %(picking)s was automatically validated based on Synapse shipment %(orderid)s."
                        )
                        % {
                            "picking": picking.name,
                            "orderid": self.orderid,
                        }
                    )
            picking.message_post(
                body=_(
                    "Delivery %(picking)s was automatically validated based on Synapse shipment %(orderid)s."
                )
                % {
                    "picking": picking.name,
                    "orderid": self.orderid,
                }
            )
            return True
        except Exception as exc:
            _logger.exception(
                "Failed to auto-validate delivery picking %s",
                picking.name,
            )
            picking.message_post(
                body=_(
                    "Synapse reported order %(orderid)s as Shipped, but automatic delivery validation for %(picking)s encountered an error: %(error)s"
                )
                % {
                    "orderid": self.orderid,
                    "picking": picking.name,
                    "error": str(exc),
                }
            )
            return False

    def _write_back_to_order(self, order):
        """Flag the SO as shipped and copy tracking onto its delivery pickings."""
        self.ensure_one()
        if order.synapse_status != "shipped":
            # Stamp the WMS-reported ship date (fallback: today) once, on the
            # first shipment received for this order — later shipment/order
            # writes never move it. The daily Synapse report groups on this
            # field to find orders that turned "Shipped" that day.
            shipped_date = (
                self.date_shipped.date()
                if self.date_shipped
                else fields.Date.context_today(order)
            )
            order.synapse_shipped_date = shipped_date
            order.synapse_status = "shipped"
        tracking = ", ".join(
            self.line_ids.filtered("tracking_number").mapped("tracking_number")
        )
        if tracking:
            pickings = order.picking_ids.filtered(
                lambda p: p.picking_type_code == "outgoing"
                and p.state not in ("done", "cancel")
            )
            for picking in pickings:
                picking.carrier_tracking_ref = tracking

        if self.connection_id.auto_validate_delivery:
            self._auto_validate_order_delivery(order)

        return True

    def _auto_validate_order_delivery(self, order):
        """Find open outgoing delivery picking(s) for the order, update quantities
        based on Synapse shipment package lines, validate the delivery, and create
        backorders if unfulfilled quantities remain."""
        self.ensure_one()
        if not self.connection_id.auto_validate_delivery:
            return False

        # Find open outgoing pickings for this order
        pickings = order.picking_ids.filtered(
            lambda p: p.picking_type_code == "outgoing" and p.state not in ("done", "cancel")
        )
        if not pickings:
            _logger.info("No open delivery pickings to validate for order %s", order.name)
            return False

        # Build mapping of shipped quantities by product default_code
        shipped_by_item = {}
        for line in self.line_ids:
            item_code = (line.item or "").strip()
            if item_code:
                shipped_by_item[item_code] = shipped_by_item.get(item_code, 0.0) + (line.quantity or 0.0)

        total_qty_shipped = self.qty_ship or sum(shipped_by_item.values())
        picking = pickings[0]

        if picking.state == "confirmed":
            picking.action_assign()

        try:
            moves = picking.move_ids.filtered(lambda m: m.state not in ("done", "cancel"))
            if not moves:
                return False

            for move in moves:
                item_code = (move.product_id.default_code or "").strip()
                if item_code and item_code in shipped_by_item:
                    ship_qty = shipped_by_item[item_code]
                    done_qty = min(move.product_uom_qty, ship_qty)
                    move.quantity = done_qty
                    move.picked = (done_qty > 0)
                    shipped_by_item[item_code] -= done_qty
                elif not shipped_by_item and len(moves) == 1 and total_qty_shipped > 0:
                    done_qty = min(move.product_uom_qty, total_qty_shipped)
                    move.quantity = done_qty
                    move.picked = (done_qty > 0)
                else:
                    move.quantity = 0.0
                    move.picked = False

            tracking = ", ".join(
                self.line_ids.filtered("tracking_number").mapped("tracking_number")
            )
            if tracking:
                picking.carrier_tracking_ref = tracking

            picking.with_context(
                skip_backorder=True,
                cancel_backorder=False,
                from_synapse_shipment_validation=True,
            ).button_validate()

            backorders = order.picking_ids.filtered(
                lambda p: p.picking_type_code == "outgoing"
                and p.state not in ("done", "cancel")
                and p.id != picking.id
            )
            if backorders:
                order.message_post(
                    body=_(
                        "Delivery %(picking)s was automatically validated based on Synapse shipment %(orderid)s. "
                        "Backorder %(backorder)s was automatically created for remaining unfulfilled quantities."
                    )
                    % {
                        "picking": picking.name,
                        "orderid": self.orderid,
                        "backorder": ", ".join(backorders.mapped("name")),
                    }
                )
            else:
                order.message_post(
                    body=_(
                        "Delivery %(picking)s was automatically validated based on Synapse shipment %(orderid)s."
                    )
                    % {
                        "picking": picking.name,
                        "orderid": self.orderid,
                    }
                )
            return True
        except Exception as exc:
            _logger.exception(
                "Failed to auto-validate delivery for order %s (picking %s)",
                order.name,
                picking.name,
            )
            order.message_post(
                body=_(
                    "Synapse reported order %(orderid)s as Shipped, but automatic delivery validation for %(picking)s encountered an error: %(error)s"
                )
                % {
                    "orderid": self.orderid,
                    "picking": picking.name,
                    "error": str(exc),
                }
            )
            return False


class SynapseShipmentLine(models.Model):
    _name = "synapse.shipment.line"
    _description = "Synapse Shipment Package"

    shipment_id = fields.Many2one(
        "synapse.shipment", string="Shipment", required=True, ondelete="cascade"
    )
    lpid = fields.Char(string="License Plate (LPID)")
    item = fields.Char(string="Item")
    lot_number = fields.Char(string="Lot Number")
    quantity = fields.Float(string="Quantity")
    uom = fields.Char(string="UoM")
    weight = fields.Float(string="Weight")
    tracking_number = fields.Char(string="Tracking Number")
    carton_type = fields.Char(string="Carton Type")
    carton_sequence = fields.Integer(string="Carton Sequence")

