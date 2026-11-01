# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class SynapseDashboard(models.Model):
    _name = "synapse.dashboard"
    _description = "Synapse Integration Dashboard"

    name = fields.Char(default="Synapse Dashboard")

    connection_count = fields.Integer(compute="_compute_counts")
    inventory_count = fields.Integer(compute="_compute_counts")
    shipment_count = fields.Integer(compute="_compute_counts")
    order_count = fields.Integer(compute="_compute_counts")
    purchase_order_count = fields.Integer(compute="_compute_counts")
    queue_new_count = fields.Integer(compute="_compute_counts")
    queue_progress_count = fields.Integer(compute="_compute_counts")
    queue_done_count = fields.Integer(compute="_compute_counts")
    queue_failed_count = fields.Integer(compute="_compute_counts")
    log_failed_count = fields.Integer(compute="_compute_counts")

    def _compute_counts(self):
        Connection = self.env["synapse.connection"]
        Inventory = self.env["synapse.inventory.line"]
        Shipment = self.env["synapse.shipment"]
        Order = self.env["sale.order"]
        Purchase = self.env["purchase.order"]
        Job = self.env["synapse.queue.job"]
        Log = self.env["synapse.log"]
        for rec in self:
            rec.connection_count = Connection.search_count([])
            rec.inventory_count = Inventory.search_count([])
            rec.shipment_count = Shipment.search_count([])
            rec.order_count = Order.search_count([("synapse_orderid", "!=", False)])
            rec.purchase_order_count = Purchase.search_count(
                [("synapse_orderid", "!=", False)]
            )
            rec.queue_new_count = Job.search_count([("state", "=", "new")])
            rec.queue_progress_count = Job.search_count([("state", "=", "progress")])
            rec.queue_done_count = Job.search_count([("state", "=", "done")])
            rec.queue_failed_count = Job.search_count([("state", "=", "failed")])
            rec.log_failed_count = Log.search_count([("state", "=", "failed")])

    # ------------------------------------------------------------------
    # The single dashboard record (lazily created) the kanban renders.
    # ------------------------------------------------------------------
    @api.model
    def _get_dashboard(self):
        record = self.search([], limit=1)
        if not record:
            record = self.create({})
        return record

    @api.model
    def action_open_dashboard(self):
        record = self._get_dashboard()
        return {
            "type": "ir.actions.act_window",
            "name": _("Synapse Dashboard"),
            "res_model": "synapse.dashboard",
            "view_mode": "kanban",
            "domain": [("id", "=", record.id)],
            "target": "current",
        }

    # ------------------------------------------------------------------
    # Card actions
    # ------------------------------------------------------------------
    def _window(self, name, model, domain=None, view_mode="list,form"):
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": model,
            "view_mode": view_mode,
            "domain": domain or [],
            "target": "current",
        }

    def get_connections(self):
        return self._window(_("Connections"), "synapse.connection")

    def get_inventory(self):
        return self._window(
            _("Inventory"), "synapse.inventory.line", view_mode="list"
        )

    def get_shipments(self):
        return self._window(_("Shipments"), "synapse.shipment")

    def get_orders(self):
        return self._window(
            _("Synapse Orders"),
            "sale.order",
            domain=[("synapse_orderid", "!=", False)],
        )

    def get_purchase_orders(self):
        return self._window(
            _("Synapse Inbound Orders"),
            "purchase.order",
            domain=[("synapse_orderid", "!=", False)],
        )

    def get_queue_new(self):
        return self._window(
            _("Queue: New"), "synapse.queue.job", domain=[("state", "=", "new")]
        )

    def get_queue_progress(self):
        return self._window(
            _("Queue: In Progress"),
            "synapse.queue.job",
            domain=[("state", "=", "progress")],
        )

    def get_queue_done(self):
        return self._window(
            _("Queue: Done"), "synapse.queue.job", domain=[("state", "=", "done")]
        )

    def get_queue_failed(self):
        return self._window(
            _("Queue: Failed"),
            "synapse.queue.job",
            domain=[("state", "=", "failed")],
        )

    def get_failed_logs(self):
        return self._window(
            _("Failed Logs"), "synapse.log", domain=[("state", "=", "failed")]
        )

    def action_process_queue(self):
        self.env["synapse.queue.job"].cron_process_queue()
        return {
            "type": "ir.actions.client",
            "tag": "reload",
        }

    def action_reconcile_synapse(self):
        """Retry stuck sends/receipts and force-correct inventory variances
        for the default connection; notify the affected salespeople and
        fulfillment@baghouseamerica.com."""
        connection = self.env["synapse.connection"]._get_default_connection()
        return connection.action_reconcile_synapse()

