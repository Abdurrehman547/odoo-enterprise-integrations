# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class ResPartner(models.Model):
    _inherit = 'res.partner'

    dropbox_folder_link = fields.Char(string="Dropbox Folder Link")
    dropbox_folder_path = fields.Char(string="Dropbox Folder Path", copy=False)

    def _get_dropbox_folder_path(self):
        """Return the Dropbox folder path for this partner."""
        self.ensure_one()
        if self.dropbox_folder_path:
            return self.dropbox_folder_path
        if self.dropbox_folder_link:
            ICP = self.env['ir.config_parameter'].sudo()
            root_path = (ICP.get_param('dropbox_integration.folder_path') or '').strip().strip('/')
            folder_name = (self.name or '').strip().strip('/')
            path = f"/{root_path}/{folder_name}" if root_path else f"/{folder_name}"
            self.sudo().dropbox_folder_path = path
            return path
        return False

    def action_open_dropbox_link(self):
        self.ensure_one()
        if not self.dropbox_folder_link:
            return
        return {
            'type': 'ir.actions.act_url',
            'url': self.dropbox_folder_link,
            'target': 'new',
        }

    def action_create_dropbox_wizard(self):
        """Open the folder-creation wizard."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Create Dropbox Folder',
            'res_model': 'dropbox.folder.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_partner_id': self.id,
                'default_res_model': 'res.partner',
                'default_res_id': self.id,
                'active_model': 'res.partner',
                'active_id': self.id,
            }
        }

    def action_upload_dropbox_wizard(self):
        """Open wizard to upload attachments to this contact's Dropbox folder."""
        self.ensure_one()
        if not self.dropbox_folder_link and not self.dropbox_folder_path:
            raise UserError(_("Please create a Dropbox folder for this contact first."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Upload to Dropbox'),
            'res_model': 'dropbox.upload.wizard',
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
