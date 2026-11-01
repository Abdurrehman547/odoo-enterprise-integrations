# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    dropbox_file_id = fields.Char(string="Dropbox File ID", readonly=True, copy=False)
    dropbox_file_link = fields.Char(string="Dropbox File Link", readonly=True, copy=False)

    def _resolve_dropbox_folder_path(self, record):
        """Helper to find Dropbox folder path from a record or its partner."""
        if not record:
            return False
        partner = False
        if record._name == 'res.partner':
            partner = record
        elif hasattr(record, 'partner_id') and record.partner_id:
            partner = record.partner_id

        # 1. Partner folder path
        if partner and hasattr(partner, '_get_dropbox_folder_path'):
            path = partner._get_dropbox_folder_path()
            if path:
                return path
        # 2. Record folder path
        if hasattr(record, '_get_dropbox_folder_path'):
            path = record._get_dropbox_folder_path()
            if path:
                return path
        # 3. Direct field on record
        if hasattr(record, 'dropbox_folder_path') and record.dropbox_folder_path:
            return record.dropbox_folder_path
        return False

    def action_upload_to_dropbox(self):
        """Upload selected attachment(s) to the linked contact or record's Dropbox folder."""
        dropbox_service = self.env['dropbox.service']
        uploaded_count = 0

        for att in self:
            record = False
            if att.res_model and att.res_id and att.res_model in self.env:
                record = self.env[att.res_model].browse(att.res_id)

            folder_path = self._resolve_dropbox_folder_path(record)

            if not folder_path:
                record_name = record.display_name if (record and hasattr(record, 'display_name')) else (record.name if (record and hasattr(record, 'name')) else 'Record')
                raise UserError(_(
                    "Could not upload '%(file)s'. The linked record (%(record)s) does not have a Dropbox folder yet.",
                    file=att.name,
                    record=record_name
                ))

            res = dropbox_service.upload_attachment(folder_path, att)
            att.write({
                'dropbox_file_id': res.get('id'),
                'dropbox_file_link': res.get('url'),
            })
            uploaded_count += 1

            if record and hasattr(record, 'message_post'):
                record.message_post(
                    body=_("File has been uploaded to Dropbox: %s") % att.name
                )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Dropbox'),
                'message': _('%d attachment(s) uploaded successfully to Dropbox.', uploaded_count),
                'type': 'success',
                'sticky': False,
            }
        }

    @api.model_create_multi
    def create(self, vals_list):
        attachments = super().create(vals_list)
        try:
            auto_upload = self.env['ir.config_parameter'].sudo().get_param(
                'dropbox_integration.auto_upload')
            if auto_upload and auto_upload in ('True', 'true', '1', True):
                for att in attachments:
                    if att.res_model and att.res_id and not att.dropbox_file_id and att.res_model in self.env:
                        record = self.env[att.res_model].browse(att.res_id)
                        if not record.exists():
                            continue

                        folder_path = self._resolve_dropbox_folder_path(record)
                        if folder_path:
                            res = self.env['dropbox.service'].upload_attachment(folder_path, att)
                            att.sudo().write({
                                'dropbox_file_id': res.get('id'),
                                'dropbox_file_link': res.get('url'),
                            })
                            if hasattr(record, 'message_post'):
                                record.message_post(
                                    body=_("File has been uploaded to Dropbox: %s") % att.name
                                )
        except Exception as e:
            _logger.warning("Dropbox auto-upload failed for attachment: %s", e)
        return attachments
