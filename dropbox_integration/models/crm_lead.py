# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    dropbox_folder_link = fields.Char(
        string="Dropbox Folder Link",
        related='partner_id.dropbox_folder_link',
        readonly=False,
    )
    dropbox_folder_path = fields.Char(
        string="Dropbox Folder Path",
        related='partner_id.dropbox_folder_path',
        readonly=False,
    )

    def _get_dropbox_folder_path(self):
        """Return the Dropbox folder path for this lead/partner."""
        self.ensure_one()
        if self.dropbox_folder_path:
            return self.dropbox_folder_path
        if self.partner_id and hasattr(self.partner_id, '_get_dropbox_folder_path'):
            return self.partner_id._get_dropbox_folder_path()
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
        """Open the folder-creation wizard for the lead."""
        self.ensure_one()
        partner = self.partner_id
        default_name = partner.name if partner else (self.name or 'Folder')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Create Dropbox Folder',
            'res_model': 'dropbox.folder.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': 'crm.lead',
                'default_res_id': self.id,
                'default_partner_id': self.partner_id.id if self.partner_id else False,
                'default_name': default_name,
                'active_model': 'crm.lead',
                'active_id': self.id,
            }
        }

    def action_upload_dropbox_wizard(self):
        """Open wizard to upload attachments to Dropbox for this lead."""
        self.ensure_one()
        if not self.dropbox_folder_link and not self._get_dropbox_folder_path():
            raise UserError(_("Please create a Dropbox folder first."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Upload to Dropbox'),
            'res_model': 'dropbox.upload.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_partner_id': self.partner_id.id if self.partner_id else False,
                'default_res_model': 'crm.lead',
                'default_res_id': self.id,
                'default_dropbox_folder_link': self.dropbox_folder_link,
                'active_model': 'crm.lead',
                'active_id': self.id,
            },
        }
