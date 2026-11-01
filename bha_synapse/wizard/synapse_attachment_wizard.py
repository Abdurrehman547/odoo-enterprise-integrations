# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class SynapseAttachmentUploadWizard(models.TransientModel):
    _name = "synapse.attachment.upload.wizard"
    _description = "Upload a File to Synapse"

    picking_id = fields.Many2one(
        "stock.picking", string="Delivery Order", readonly=True
    )
    sale_order_id = fields.Many2one(
        "sale.order", string="Sale Order", readonly=True
    )
    connection_id = fields.Many2one(
        "synapse.connection",
        string="Connection",
        required=True,
        default=lambda self: self.env["synapse.connection"]._get_default_connection(),
    )
    attachment_file = fields.Binary(string="File", required=True)
    filename = fields.Char(string="File Name")

    def action_upload(self):
        """Create an ir.attachment from the chosen file and QUEUE its upload to
        Synapse instead of pushing it synchronously. The queue processor runs
        upload_order_attachments, which sends this file together with every
        other not-yet-uploaded attachment (labels, packing slip)
        and stamps each one ``synapse_up:<file_id>`` after the upload."""
        self.ensure_one()
        target = self.picking_id or self.sale_order_id
        if not target:
            raise UserError(_("No delivery order or sale order selected."))
        if not target.synapse_orderid:
            raise UserError(
                _("%(target)s has not been sent to Synapse yet; cannot upload attachments.")
                % {"target": target.name}
            )
        if not self.attachment_file:
            raise UserError(_("Please choose a file to upload."))
        connection = self.connection_id or target._synapse_connection()
        if not connection:
            raise UserError(_("No Synapse connection is configured."))

        self.env["ir.attachment"].create(
            {
                "name": self.filename or "synapse-upload",
                "datas": self.attachment_file,
                "res_model": target._name,
                "res_id": target.id,
            }
        )
        job = self.env["synapse.queue.job"]._enqueue(
            connection,
            "attachment",
            "export",
            name=_("Upload File %s") % (self.filename or target.name),
            res_model=target._name,
            res_id=target.id,
            synapse_ref=target.synapse_orderid,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "sticky": False,
                "message": _(
                    "File '%(name)s' queued for upload to Synapse for %(target)s "
                    "(job: %(job)s). It will be sent when the queue is "
                    "processed."
                )
                % {"name": self.filename or "synapse-upload",
                   "target": target.name, "job": job.name},
            },
        }
