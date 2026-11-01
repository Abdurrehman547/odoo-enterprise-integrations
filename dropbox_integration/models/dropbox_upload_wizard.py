# -*- coding: utf-8 -*-
import base64
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DropboxUploadWizard(models.TransientModel):
    _name = 'dropbox.upload.wizard'
    _description = 'Upload Attachments to Dropbox'

    partner_id = fields.Many2one('res.partner', string='Contact', readonly=True)
    res_model = fields.Char(string='Resource Model')
    res_id = fields.Integer(string='Resource ID')
    dropbox_folder_link = fields.Char(string='Dropbox Folder', readonly=True)

    attachment_ids = fields.Many2many(
        'ir.attachment',
        'dropbox_upload_wizard_attachment_rel',
        'wizard_id',
        'attachment_id',
        string='Attachments to Upload',
        help="Select which contact attachments to upload to Dropbox."
    )
    has_existing_attachments = fields.Boolean(
        compute='_compute_has_existing_attachments',
        string='Has Existing Attachments'
    )

    upload_new_file = fields.Boolean(
        string='Upload a New File',
        default=False,
        help="Enable this to upload and attach a new file from your device."
    )
    file_data = fields.Binary(string='File')
    file_name = fields.Char(string='File Name')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self.env.context
        active_model = ctx.get('active_model') or ctx.get('default_res_model') or 'res.partner'
        active_id = ctx.get('active_id') or ctx.get('default_res_id')

        partner = False
        if active_model == 'res.partner' and active_id:
            partner = self.env['res.partner'].browse(active_id)
        elif ctx.get('default_partner_id'):
            partner = self.env['res.partner'].browse(ctx['default_partner_id'])
        elif active_model and active_id and active_model in self.env:
            rec = self.env[active_model].browse(active_id)
            if hasattr(rec, 'partner_id') and rec.partner_id:
                partner = rec.partner_id

        if partner and partner.exists():
            res['partner_id'] = partner.id
            res['dropbox_folder_link'] = partner.dropbox_folder_link
        elif active_model and active_id and active_model in self.env:
            rec = self.env[active_model].browse(active_id)
            if hasattr(rec, 'dropbox_folder_link') and rec.dropbox_folder_link:
                res['dropbox_folder_link'] = rec.dropbox_folder_link

        res['res_model'] = active_model
        res['res_id'] = active_id

        # Pre-select existing attachments on this record
        if active_model and active_id:
            attachments = self.env['ir.attachment'].search([
                ('res_model', '=', active_model),
                ('res_id', '=', active_id),
            ])
            if attachments:
                res['attachment_ids'] = [(6, 0, attachments.ids)]
            else:
                res['upload_new_file'] = True

        return res

    @api.depends('res_model', 'res_id')
    def _compute_has_existing_attachments(self):
        for wiz in self:
            if wiz.res_model and wiz.res_id:
                count = self.env['ir.attachment'].search_count([
                    ('res_model', '=', wiz.res_model),
                    ('res_id', '=', wiz.res_id),
                ])
                wiz.has_existing_attachments = bool(count)
            else:
                wiz.has_existing_attachments = bool(wiz.attachment_ids)

    def action_upload(self):
        """Upload selected attachments and/or new file to Dropbox."""
        self.ensure_one()
        partner = self.partner_id
        target_record = False
        if self.res_model and self.res_id and self.res_model in self.env:
            target_record = self.env[self.res_model].browse(self.res_id)

        folder_path = False
        if partner:
            folder_path = partner._get_dropbox_folder_path()
        if not folder_path and target_record and hasattr(target_record, '_get_dropbox_folder_path'):
            folder_path = target_record._get_dropbox_folder_path()
        if not folder_path and target_record and hasattr(target_record, 'dropbox_folder_path') and target_record.dropbox_folder_path:
            folder_path = target_record.dropbox_folder_path

        if not folder_path:
            raise UserError(_(
                "Could not find the Dropbox folder path for this contact. "
                "Please make sure the Dropbox folder is created first."))

        uploaded_files = []
        dropbox_service = self.env['dropbox.service']

        # 1. Upload selected existing attachments
        for att in self.attachment_ids:
            res = dropbox_service.upload_attachment(folder_path, att)
            if hasattr(att, 'dropbox_file_id'):
                att.sudo().write({
                    'dropbox_file_id': res.get('id'),
                    'dropbox_file_link': res.get('url'),
                })
            uploaded_files.append((att.name, res.get('url')))

        # 2. Upload newly attached file (if provided)
        if self.upload_new_file and self.file_data:
            if not self.file_name:
                raise UserError(_("Please provide a name for the uploaded file."))
            file_bytes = base64.b64decode(self.file_data)
            # Create ir.attachment on the target record
            new_att = self.env['ir.attachment'].create({
                'name': self.file_name,
                'datas': self.file_data,
                'res_model': self.res_model or 'res.partner',
                'res_id': self.res_id or (partner.id if partner else 0),
            })
            res = dropbox_service.upload_file(
                folder_path=folder_path,
                file_name=self.file_name,
                file_content=file_bytes,
            )
            if hasattr(new_att, 'dropbox_file_id'):
                new_att.sudo().write({
                    'dropbox_file_id': res.get('id'),
                    'dropbox_file_link': res.get('url'),
                })
            uploaded_files.append((self.file_name, res.get('url')))

        if not uploaded_files:
            raise UserError(_("Please select at least one attachment or choose a file to upload."))

        # Post message to chatter if available
        post_target = target_record if (target_record and hasattr(target_record, 'message_post')) else (partner if (partner and hasattr(partner, 'message_post')) else False)
        if post_target:
            file_names = ", ".join([name for name, _link in uploaded_files])
            if len(uploaded_files) == 1:
                msg = _("File has been uploaded to Dropbox: %s") % file_names
            else:
                msg = _("Files uploaded to Dropbox: %s") % file_names
            post_target.message_post(body=msg)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Dropbox Upload'),
                'message': _('%(count)d file(s) uploaded successfully to Dropbox.', count=len(uploaded_files)),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            }
        }
