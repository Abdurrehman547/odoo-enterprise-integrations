# -*- coding: utf-8 -*-
from datetime import datetime

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class SynapseShipmentTests(TransactionCase):
    """Shipment sync, status codes and the shipment -> sale order link."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.connection = cls.env["synapse.connection"].create(
            {"name": "Test Connection"}
        )

    def _create_order(self, **kwargs):
        values = {"partner_id": self.env.ref("base.partner_admin").id}
        values.update(kwargs)
        return self.env["sale.order"].create(values)

    # ------------------------------------------------------------------
    # Helpers: status normalization and date parsing
    # ------------------------------------------------------------------
    def test_normalize_order_status(self):
        normalize = self.env["synapse.shipment"]._normalize_order_status
        self.assertEqual(normalize("09"), "9")
        self.assertEqual(normalize("9"), "9")
        self.assertEqual(normalize("0"), "0")  # Hold must keep its code
        self.assertEqual(normalize(9), "9")
        self.assertEqual(normalize("A"), "A")
        self.assertFalse(normalize(None))
        self.assertFalse(normalize("   "))

    def test_parse_dt(self):
        parse = self.env["synapse.shipment"]._parse_dt
        self.assertEqual(
            parse("09/11/2026 16:33:45"), datetime(2026, 9, 11, 16, 33, 45)
        )
        self.assertEqual(parse("09/11/2026"), datetime(2026, 9, 11))
        self.assertFalse(parse("not-a-date"))
        self.assertFalse(parse(""))

    def test_order_status_label(self):
        ship = self.env["synapse.shipment"].create(
            {
                "connection_id": self.connection.id,
                "orderid": "1001",
                "order_status": "9",
            }
        )
        self.assertEqual(ship.order_status_label, "Shipped")
        ship.order_status = "5"
        self.assertEqual(ship.order_status_label, "Picking")
        ship.order_status = "Z"  # unknown code falls back to the raw value
        self.assertEqual(ship.order_status_label, "Z")

    # ------------------------------------------------------------------
    # Sale order matching
    # ------------------------------------------------------------------
    def test_match_sale_order_by_synapse_orderid(self):
        order = self._create_order(synapse_orderid="733785")
        row = {"orderid": "733785", "reference": "DOES-NOT-MATCH", "po_number": False}
        self.assertEqual(self.env["synapse.shipment"]._match_sale_order(row), order)

    def test_match_sale_order_by_reference_fallback(self):
        order = self._create_order(client_order_ref="PO-123")
        row = {"orderid": "999", "reference": "PO-123", "po_number": False}
        self.assertEqual(self.env["synapse.shipment"]._match_sale_order(row), order)

    def test_match_sale_order_no_match(self):
        row = {"orderid": "999", "reference": "NOPE", "po_number": False}
        self.assertFalse(self.env["synapse.shipment"]._match_sale_order(row))

    # ------------------------------------------------------------------
    # Upsert: create/update + linking + order write-back
    # ------------------------------------------------------------------
    def test_upsert_creates_links_and_normalizes(self):
        order = self._create_order(synapse_orderid="733785")
        Shipment = self.env["synapse.shipment"]
        ship = Shipment._upsert_from_synapse(
            {
                "orderid": "733785",
                "shipid": "1",
                "reference": "SO-X",
                "order_status": "09",
                "date_shipped": "09/11/2026 16:33:45",
                "qty_ship": 32,
                "plate_details": [],
            },
            self.connection,
        )
        self.assertEqual(ship.order_status, "9")  # '09' normalized
        self.assertEqual(ship.sale_order_id, order)
        self.assertEqual(order.synapse_status, "shipped")
        self.assertEqual(order.synapse_shipped_date, fields.Date.to_date("2026-09-11"))

        # A second upsert of the same row updates in place, never duplicates.
        ship2 = Shipment._upsert_from_synapse(
            {
                "orderid": "733785",
                "shipid": "1",
                "reference": "SO-X",
                "order_status": "9",
                "plate_details": [],
            },
            self.connection,
        )
        self.assertEqual(ship, ship2)
        self.assertEqual(Shipment.search_count([("orderid", "=", "733785")]), 1)

    # ------------------------------------------------------------------
    # Smart button
    # ------------------------------------------------------------------
    def test_resolve_sale_order_persists_link(self):
        order = self._create_order(synapse_orderid="733785")
        ship = self.env["synapse.shipment"].create(
            {
                "connection_id": self.connection.id,
                "orderid": "733785",
                "reference": "SO-X",
            }
        )
        self.assertFalse(ship.sale_order_id)
        self.assertEqual(ship._resolve_sale_order(), order)
        self.assertEqual(ship.sale_order_id, order)

    def test_action_view_sale_order(self):
        order = self._create_order(synapse_orderid="733785")
        ship = self.env["synapse.shipment"].create(
            {
                "connection_id": self.connection.id,
                "orderid": "733785",
                "sale_order_id": order.id,
            }
        )
        action = ship.action_view_sale_order()
        self.assertEqual(action["res_model"], "sale.order")
        self.assertEqual(action["res_id"], order.id)

    def test_action_view_sale_order_no_match_raises(self):
        ship = self.env["synapse.shipment"].create(
            {
                "connection_id": self.connection.id,
                "orderid": "424242",
                "reference": "NOMATCH",
            }
        )
        with self.assertRaises(UserError):
            ship.action_view_sale_order()
