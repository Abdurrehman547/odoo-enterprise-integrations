# -*- coding: utf-8 -*-
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class SynapsePackingSlipTests(TransactionCase):
    """Test auto-sending packing slips after order is sent to Synapse and manual trigger."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.connection = cls.env["synapse.connection"].create({
            "name": "Test Synapse Connection",
            "auto_send_packing_slip": True,
        })
        cls.order = cls.env["sale.order"].create({
            "partner_id": cls.env.ref("base.partner_admin").id,
            "synapse_connection_id": cls.connection.id,
            "synapse_orderid": "999888",
        })

    def test_auto_send_packing_slip_flag(self):
        """Test toggling the auto_send_packing_slip configuration."""
        self.assertTrue(self.connection.auto_send_packing_slip)
        self.connection.auto_send_packing_slip = False
        self.assertFalse(self.connection.auto_send_packing_slip)

    @patch.object(type(connection), "upload_order_attachments")
    def test_order_sent_auto_send_enabled(self, mock_upload):
        """When order has been sent to Synapse and flag is True, packing slip upload is called."""
        self.connection.auto_send_packing_slip = True
        self.order.synapse_customer_packing_list = False
        self.connection._auto_send_packing_slip(self.order)
        mock_upload.assert_called_once_with(self.order)

    @patch.object(type(connection), "upload_order_attachments")
    def test_order_sent_auto_send_disabled(self, mock_upload):
        """When auto_send_packing_slip is False, upload is not triggered after sending order."""
        self.connection.auto_send_packing_slip = False
        self.order.synapse_customer_packing_list = False
        self.connection._auto_send_packing_slip(self.order)
        mock_upload.assert_not_called()

    @patch.object(type(connection), "upload_order_attachments")
    def test_order_sent_customer_packing_list_skipped(self, mock_upload):
        """When customer packing list is set, upload is skipped after sending order."""
        self.connection.auto_send_packing_slip = True
        self.order.synapse_customer_packing_list = True
        self.connection._auto_send_packing_slip(self.order)
        mock_upload.assert_not_called()

    @patch.object(type(connection), "upload_order_attachments")
    def test_delivery_validation_does_not_send_packing_slip(self, mock_upload):
        """Delivery validation (_action_done) must NOT trigger packing slip upload anymore."""
        picking_type = self.env["stock.picking.type"].search([("code", "=", "outgoing")], limit=1)
        picking = self.env["stock.picking"].create({
            "picking_type_id": picking_type.id,
            "location_id": picking_type.default_location_src_id.id,
            "location_dest_id": self.env.ref("stock.stock_location_customers").id,
            "sale_id": self.order.id,
        })
        self.connection.auto_send_packing_slip = True
        # Calling _action_done on picking must not trigger packing slip upload
        picking._action_done()
        mock_upload.assert_not_called()

    def test_auto_validate_delivery_flag(self):
        """Test toggling the auto_validate_delivery configuration."""
        self.assertFalse(self.connection.auto_validate_delivery)
        self.connection.auto_validate_delivery = True
        self.assertTrue(self.connection.auto_validate_delivery)

    def test_auto_validate_delivery_skipped_when_disabled(self):
        """When auto_validate_delivery is False, shipment _write_back_to_order does not validate delivery."""
        self.connection.auto_validate_delivery = False
        shipment = self.env["synapse.shipment"].create({
            "connection_id": self.connection.id,
            "orderid": "999888",
            "sale_order_id": self.order.id,
        })
        with patch.object(type(shipment), "_auto_validate_order_delivery") as mock_val:
            shipment._write_back_to_order(self.order)
            mock_val.assert_not_called()

    def test_auto_validate_delivery_called_when_enabled(self):
        """When auto_validate_delivery is True, shipment _write_back_to_order triggers validation."""
        self.connection.auto_validate_delivery = True
        shipment = self.env["synapse.shipment"].create({
            "connection_id": self.connection.id,
            "orderid": "999888",
            "sale_order_id": self.order.id,
        })
        with patch.object(type(shipment), "_auto_validate_order_delivery") as mock_val:
            shipment._write_back_to_order(self.order)
            mock_val.assert_called_once_with(self.order)

