# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    gdrive_folder_link = fields.Char(
        string="Google Drive Folder Link",
        related='partner_id.gdrive_folder_link',
        readonly=False,
    )
    gdrive_folder_id = fields.Char(
        string="Google Drive Folder ID",
        related='partner_id.gdrive_folder_id',
        readonly=False,
    )

    def _get_gdrive_folder_id(self):
        """Return the Google Drive folder ID for this lead/partner."""
        self.ensure_one()
        if self.gdrive_folder_id:
            return self.gdrive_folder_id
        if self.partner_id and hasattr(self.partner_id, '_get_gdrive_folder_id'):
            return self.partner_id._get_gdrive_folder_id()
        if self.gdrive_folder_link:
            return self.env['google.drive.service'].extract_folder_id_from_url(
                self.gdrive_folder_link)
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
        """Open the folder-creation wizard for the lead."""
        self.ensure_one()
        partner = self.partner_id
        default_name = partner.name if partner else (self.name or 'Folder')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Create Google Drive Folder',
            'res_model': 'google.drive.folder.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': 'crm.lead',
                'default_res_id': self.id,
                'default_partner_id': self.partner_id.id if self.partner_id else False,
                'default_name': default_name,
                'active_model': 'crm.lead',
                'active_id': self.id,
            },
        }

    def action_upload_gdrive_wizard(self):
        """Open the wizard to upload attachments to Google Drive for this lead."""
        self.ensure_one()
        if not self.gdrive_folder_link and not self._get_gdrive_folder_id():
            raise UserError(_("Please create a Google Drive folder first."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Upload to Google Drive'),
            'res_model': 'google.drive.upload.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_partner_id': self.partner_id.id if self.partner_id else False,
                'default_res_model': 'crm.lead',
                'default_res_id': self.id,
                'default_gdrive_folder_link': self.gdrive_folder_link,
                'active_model': 'crm.lead',
                'active_id': self.id,
            },
        }
