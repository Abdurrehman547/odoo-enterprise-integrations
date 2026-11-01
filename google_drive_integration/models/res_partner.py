# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class ResPartner(models.Model):
    _inherit = 'res.partner'

    gdrive_folder_link = fields.Char(string="Google Drive Folder Link")
    gdrive_folder_id = fields.Char(string="Google Drive Folder ID", copy=False)

    def _get_gdrive_folder_id(self):
        """Return the Google Drive folder ID for this partner, extracting and
        caching from gdrive_folder_link if gdrive_folder_id is not yet set."""
        self.ensure_one()
        if self.gdrive_folder_id:
            return self.gdrive_folder_id
        if self.gdrive_folder_link:
            folder_id = self.env['google.drive.service'].extract_folder_id_from_url(
                self.gdrive_folder_link)
            if folder_id:
                self.sudo().gdrive_folder_id = folder_id
                return folder_id
        return False

    def action_open_gdrive_link(self):
        self.ensure_one()
        if not self.gdrive_folder_link:
            return
        return {
            'type': 'ir.actions.act_url',
            'url': self.gdrive_folder_link,
            'target': 'new',
        }

    def action_create_gdrive_wizard(self):
        """Open the folder-creation wizard."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Create Google Drive Folder',
            'res_model': 'google.drive.folder.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_partner_id': self.id,
                'active_model': 'res.partner',
                'active_id': self.id,
            },
        }

    def action_upload_gdrive_wizard(self):
        """Open the wizard to upload attachments to this contact's Google Drive folder."""
        self.ensure_one()
        if not self.gdrive_folder_link and not self.gdrive_folder_id:
            raise UserError(_("Please create a Google Drive folder for this contact first."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Upload to Google Drive'),
            'res_model': 'google.drive.upload.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_partner_id': self.id,
                'default_res_model': 'res.partner',
                'default_res_id': self.id,
                'active_model': 'res.partner',
                'active_id': self.id,
            },
        }
