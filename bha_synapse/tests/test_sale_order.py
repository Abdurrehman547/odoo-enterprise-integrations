# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class SaleOrderSynapseTests(TransactionCase):
    """Sale order side: the Shipments smart button count and action."""

    def _create_order(self, **kwargs):
        values = {"partner_id": self.env.ref("base.partner_admin").id}
        values.update(kwargs)
        return self.env["sale.order"].create(values)

    def _create_connection(self):
        return self.env["synapse.connection"].create({"name": "Test Connection"})

    def _create_shipment(self, connection, **kwargs):
        values = {"connection_id": connection.id, "orderid": "1"}
        values.update(kwargs)
        return self.env["synapse.shipment"].create(values)

    def test_shipment_count_with_stored_link(self):
        connection = self._create_connection()
        order = self._create_order()
        self._create_shipment(connection, orderid="11", sale_order_id=order.id)
        self._create_shipment(connection, orderid="12", sale_order_id=order.id)
        self.assertEqual(order.synapse_shipment_count, 2)

    def test_shipment_count_orderid_fallback(self):
        connection = self._create_connection()
        order = self._create_order(synapse_orderid="733785")
        # Not linked, but carries the order's Synapse order id — must count.
        self._create_shipment(connection, orderid="733785", reference="SO-X")
        self.assertEqual(order.synapse_shipment_count, 1)

    def test_no_shipments_means_zero_count(self):
        order = self._create_order()
        self.assertEqual(order.synapse_shipment_count, 0)

    def test_action_view_synapse_shipments(self):
        connection = self._create_connection()
        order = self._create_order()
        self._create_shipment(connection, orderid="11", sale_order_id=order.id)
        action = order.action_view_synapse_shipments()
        self.assertEqual(action["res_model"], "synapse.shipment")
        self.assertIn(("sale_order_id", "=", order.id), action["domain"])
