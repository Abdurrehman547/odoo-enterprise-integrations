# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    gdrive_file_id = fields.Char(string="Google Drive File ID", readonly=True, copy=False)
    gdrive_file_link = fields.Char(string="Google Drive File Link", readonly=True, copy=False)

    def _resolve_gdrive_folder_id(self, record):
        """Helper to find Google Drive folder ID from a record or its partner."""
        if not record:
            return False
        partner = False
        if record._name == 'res.partner':
            partner = record
        elif hasattr(record, 'partner_id') and record.partner_id:
            partner = record.partner_id

        # 1. Partner folder ID
        if partner and hasattr(partner, '_get_gdrive_folder_id'):
            fid = partner._get_gdrive_folder_id()
            if fid:
                return fid
        # 2. Record folder ID
        if hasattr(record, '_get_gdrive_folder_id'):
            fid = record._get_gdrive_folder_id()
            if fid:
                return fid
        # 3. Extract from record link
        if hasattr(record, 'gdrive_folder_link') and record.gdrive_folder_link:
            return self.env['google.drive.service'].extract_folder_id_from_url(record.gdrive_folder_link)
        return False

    def action_upload_to_gdrive(self):
        """Upload selected attachment(s) to the linked contact or record's Google Drive folder."""
        gdrive_service = self.env['google.drive.service']
        uploaded_count = 0

        for att in self:
            record = False
            if att.res_model and att.res_id and att.res_model in self.env:
                record = self.env[att.res_model].browse(att.res_id)

            folder_id = self._resolve_gdrive_folder_id(record)

            if not folder_id:
                record_name = record.display_name if (record and hasattr(record, 'display_name')) else (record.name if (record and hasattr(record, 'name')) else 'Record')
                raise UserError(_(
                    "Could not upload '%(file)s'. The linked record (%(record)s) does not have a Google Drive folder yet.",
                    file=att.name,
                    record=record_name
                ))

            res = gdrive_service.upload_attachment(folder_id, att)
            att.write({
                'gdrive_file_id': res.get('id'),
                'gdrive_file_link': res.get('webViewLink'),
            })
            uploaded_count += 1

            if record and hasattr(record, 'message_post'):
                record.message_post(
                    body=_("File has been uploaded to Google Drive: %s") % att.name
                )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Google Drive'),
                'message': _('%d attachment(s) uploaded successfully to Google Drive.', uploaded_count),
                'type': 'success',
                'sticky': False,
            }
        }

    @api.model_create_multi
    def create(self, vals_list):
        attachments = super().create(vals_list)
        try:
            auto_upload = self.env['ir.config_parameter'].sudo().get_param(
                'google_drive_integration.auto_upload')
            if auto_upload and auto_upload in ('True', 'true', '1', True):
                for att in attachments:
                    if att.res_model and att.res_id and not att.gdrive_file_id and att.res_model in self.env:
                        record = self.env[att.res_model].browse(att.res_id)
                        if not record.exists():
                            continue

                        folder_id = self._resolve_gdrive_folder_id(record)
                        if folder_id:
                            res = self.env['google.drive.service'].upload_attachment(folder_id, att)
                            att.sudo().write({
                                'gdrive_file_id': res.get('id'),
                                'gdrive_file_link': res.get('webViewLink'),
                            })
                            if hasattr(record, 'message_post'):
                                record.message_post(
                                    body=_("File has been uploaded to Google Drive: %s") % att.name
                                )
        except Exception as e:
            _logger.warning("Google Drive auto-upload failed for attachment: %s", e)
        return attachments
