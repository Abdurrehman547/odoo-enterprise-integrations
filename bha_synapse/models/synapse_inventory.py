# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class SynapseInventoryLine(models.Model):
    _name = "synapse.inventory.line"
    _description = "Synapse Inventory Snapshot"
    _order = "item, lot_number"

    connection_id = fields.Many2one(
        "synapse.connection", string="Connection", required=True, ondelete="cascade"
    )
    facility = fields.Char(string="Facility", index=True)
    custid = fields.Char(string="Customer ID")
    item = fields.Char(string="Item", index=True, required=True)
    lot_number = fields.Char(string="Lot Number")
    uom = fields.Char(string="UoM")
    uom_desc = fields.Char(string="UoM Description")
    inventory_class = fields.Char(string="Inventory Class")
    inventory_class_desc = fields.Char(string="Inventory Class Desc.")
    inventory_status = fields.Char(string="Inventory Status")
    inventory_status_desc = fields.Char(string="Inventory Status Desc.")
    status = fields.Char(string="Status")
    status_desc = fields.Char(string="Status Desc.")
    lip_count = fields.Integer(string="LIP Count")
    qty = fields.Float(string="Quantity")
    weight = fields.Float(string="Weight")
    cube = fields.Float(string="Cube")

    product_id = fields.Many2one("product.product", string="Odoo Product")
    odoo_qty = fields.Float(
        string="Odoo On Hand",
        compute="_compute_odoo_qty_and_variance",
        readonly=True,
        help="Odoo's on-hand quantity at this connection's Synapse-mirror "
        "location only (not the product's company-wide total) — so it's "
        "comparable apples-to-apples with the Synapse quantity. A product "
        "also stocked elsewhere in Odoo won't show a false variance here.",
    )
    variance = fields.Float(
        string="Variance", compute="_compute_odoo_qty_and_variance", store=False
    )
    matched = fields.Boolean(string="Matched", compute="_compute_matched", store=True)
    last_sync = fields.Datetime(string="Last Sync", readonly=True)

    @api.depends("product_id")
    def _compute_matched(self):
        for line in self:
            line.matched = bool(line.product_id)

    @api.depends("product_id", "qty", "connection_id.inventory_location_id")
    def _compute_odoo_qty_and_variance(self):
        """Batched: one stock.quant query per distinct Synapse-mirror
        location, not one per line — this is a live (store=False) compute,
        so a naive per-line query would re-run for every line every time the
        Inventory Items list is opened or reconcile_inventory() scans it."""
        by_location = {}
        for line in self:
            by_location.setdefault(line.connection_id.inventory_location_id, []).append(line)
        Quant = self.env["stock.quant"].sudo()
        for location, lines in by_location.items():
            products = self.env["product.product"].browse(
                list({l.product_id.id for l in lines if l.product_id})
            )
            qty_by_product = {}
            if location and products:
                quants = Quant.search(
                    [
                        ("product_id", "in", products.ids),
                        ("location_id", "child_of", location.id),
                    ]
                )
                for quant in quants:
                    qty_by_product[quant.product_id.id] = (
                        qty_by_product.get(quant.product_id.id, 0.0) + quant.quantity
                    )
            for line in lines:
                odoo_qty = (
                    qty_by_product.get(line.product_id.id, 0.0)
                    if line.product_id
                    else 0.0
                )
                line.odoo_qty = odoo_qty
                line.variance = (line.qty or 0.0) - odoo_qty

    @staticmethod
    def _match_product(env, item, company):
        if not item:
            return env["product.product"]
        return env["product.product"].search(
            [("default_code", "=", item)], limit=1
        )

    def _import_row(self, row, connection):
        """Create or update one inventory snapshot line from a Synapse row.

        Called per-record by the sync queue processor and by the direct
        ``pull_inventory`` path. Returns the affected line.
        """
        vals = self._values_from_synapse(row, connection)
        vals["last_sync"] = fields.Datetime.now()
        existing = self.search(
            [
                ("connection_id", "=", connection.id),
                ("facility", "=", vals["facility"]),
                ("item", "=", vals["item"]),
                ("lot_number", "=", vals["lot_number"]),
                ("uom", "=", vals["uom"]),
            ],
            limit=1,
        )
        if existing:
            existing.write(vals)
            line = existing
        else:
            line = self.create(vals)
        if connection.auto_reconcile_inventory:
            line._reconcile_to_odoo()
        return line

    def _values_from_synapse(self, row, connection):
        """Map one Synapse inventory row to model values."""
        product = self._match_product(self.env, row.get("item"), connection.company_id)
        return {
            "connection_id": connection.id,
            "facility": row.get("facility"),
            "custid": row.get("custid"),
            "item": row.get("item"),
            "lot_number": row.get("lot_number") or "",
            "uom": row.get("uom"),
            "uom_desc": row.get("uom_desc"),
            "inventory_class": row.get("inventory_class"),
            "inventory_class_desc": row.get("inventory_class_desc"),
            "inventory_status": row.get("inventory_status"),
            "inventory_status_desc": row.get("inventory_status_desc"),
            "status": row.get("status"),
            "status_desc": row.get("status_desc"),
            "lip_count": row.get("lip_count") or 0,
            "qty": row.get("qty") or 0.0,
            "weight": row.get("weight") or 0.0,
            "cube": row.get("cube") or 0.0,
            "product_id": product.id if product else False,
        }

    def _reconcile_to_odoo(self):
        """Set Odoo on-hand to the Synapse quantity for matched products.

        Only runs when the connection has ``auto_reconcile_inventory`` enabled
        and an inventory location configured.
        """
        Quant = self.env["stock.quant"]
        for line in self.filtered(lambda l: l.product_id):
            location = line.connection_id.inventory_location_id
            if not location:
                _logger.warning(
                    "Synapse: no inventory location set on %s; skipping reconcile "
                    "for %s",
                    line.connection_id.name,
                    line.item,
                )
                continue
            Quant.with_context(inventory_mode=True).create(
                {
                    "product_id": line.product_id.id,
                    "location_id": location.id,
                    "inventory_quantity": line.qty,
                }
            ).action_apply_inventory()
        return True

