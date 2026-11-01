# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class GoogleDriveFolderWizard(models.TransientModel):
    _name = 'google.drive.folder.wizard'
    _description = 'Google Drive Folder Creation Wizard'

    name = fields.Char(
        string='Folder Name',
        required=True,
        help="Name of the folder to create in Google Drive"
    )
    res_model = fields.Char(string='Resource Model')
    res_id = fields.Integer(string='Resource ID')
    partner_id = fields.Many2one('res.partner', string='Partner')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self.env.context
        active_model = ctx.get('active_model')
        active_id = ctx.get('active_id')

        if active_model and active_id:
            res.setdefault('res_model', active_model)
            res.setdefault('res_id', active_id)

            if active_model == 'res.partner':
                res.setdefault('partner_id', active_id)
                partner = self.env['res.partner'].browse(active_id)
                res.setdefault('name', partner.name)
            elif active_model in self.env:
                record = self.env[active_model].browse(active_id)
                if hasattr(record, 'partner_id') and record.partner_id:
                    res.setdefault('partner_id', record.partner_id.id)
                if hasattr(record, 'display_name') and record.display_name:
                    res.setdefault('name', record.display_name)
                elif hasattr(record, 'name') and record.name:
                    res.setdefault('name', record.name)
        return res

    def create_gdrive_folder(self):
        """Create folder in Google Drive and store the webViewLink and folder ID on target record(s)."""
        self.ensure_one()
        folder_info = self.env['google.drive.service'].create_folder(self.name)
        folder_url = folder_info.get('url') if hasattr(folder_info, 'get') else str(folder_info)
        folder_id = folder_info.get('id') if hasattr(folder_info, 'get') else False

        # Update specific active resource if it has the link field
        if self.res_model and self.res_id and self.res_model in self.env:
            record = self.env[self.res_model].browse(self.res_id)
            if hasattr(record, 'gdrive_folder_link'):
                record.gdrive_folder_link = folder_url
            if hasattr(record, 'gdrive_folder_id') and folder_id:
                record.gdrive_folder_id = folder_id
            if not self.partner_id and hasattr(record, 'partner_id') and record.partner_id:
                record.partner_id.gdrive_folder_link = folder_url
                if folder_id:
                    record.partner_id.gdrive_folder_id = folder_id

        # Update partner if set
        if self.partner_id:
            self.partner_id.gdrive_folder_link = folder_url
            if folder_id:
                self.partner_id.gdrive_folder_id = folder_id

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Google Drive'),
                'message': _('Folder created and link attached successfully.'),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            }
        }
