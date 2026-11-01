# -*- coding: utf-8 -*-
from odoo import api, fields, models


class SynapseLog(models.Model):
    _name = "synapse.log"
    _description = "Synapse Sync Log"
    _order = "id desc"
    _rec_name = "name"

    name = fields.Char(string="Summary")
    connection_id = fields.Many2one(
        "synapse.connection", string="Connection", ondelete="set null"
    )
    queue_job_id = fields.Many2one(
        "synapse.queue.job", string="Queue Job", ondelete="set null"
    )
    job_type = fields.Selection(
        [
            ("inventory", "Inventory"),
            ("shipment", "Shipment"),
            ("order", "Order"),
            ("update", "Update Order"),
            ("resync", "Resync Order"),
            ("attachment", "Attachment"),
            ("cancel", "Cancel Order"),
            ("purchase_order", "Purchase Order"),
            ("purchase_resync", "Resync Purchase Order"),
            ("purchase_cancel", "Cancel Purchase Order"),
            ("system", "System"),
        ],
        string="Type",
    )
    direction = fields.Selection(
        [("import", "Synapse → Odoo"), ("export", "Odoo → Synapse")],
        string="Direction",
    )
    state = fields.Selection(
        [("success", "Success"), ("failed", "Failed")],
        string="Status",
        index=True,
    )
    model = fields.Char(string="Model")
    res_ref = fields.Char(string="Record Reference")
    message = fields.Text(string="Message")

    @api.model
    def _log(
        self,
        state,
        message,
        connection=None,
        queue_job=None,
        job_type=None,
        direction=None,
        model=None,
        res_ref=None,
        name=None,
    ):
        """Create a sync log entry. Safe to call from anywhere."""
        return self.sudo().create(
            {
                "name": name or (message or "")[:120],
                "state": state,
                "message": message,
                "connection_id": connection.id if connection else False,
                "queue_job_id": queue_job.id if queue_job else False,
                "job_type": job_type,
                "direction": direction,
                "model": model,
                "res_ref": res_ref,
            }
        )

