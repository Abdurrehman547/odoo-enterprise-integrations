# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AuthenticationWizard(models.TransientModel):
    _name = 'authentication.wizard'
    _description = 'Authentication Code Wizard'

    dropbox_authorization_code = fields.Char(
        string='Dropbox Authorization Code',
        help="Authorization code obtained from Dropbox"
    )
    dropbox_auth_url = fields.Char(
        string='Dropbox Authentication URL',
        compute='_compute_dropbox_auth_url',
        help="Dropbox Authentication URL"
    )

    def _compute_dropbox_auth_url(self):
        """Compute method to generate the Dropbox Authentication URL."""
        settings = self.env['res.config.settings'].new()
        try:
            auth_url = settings.get_dropbox_auth_url()
        except Exception:
            auth_url = ''
        for rec in self:
            rec.dropbox_auth_url = auth_url

    def action_setup_dropbox_token(self):
        """Action method to set up Dropbox tokens using the authorization code."""
        self.ensure_one()
        if not self.dropbox_authorization_code or not self.dropbox_authorization_code.strip():
            raise UserError(_("Please paste the authorization code from Dropbox."))

        settings = self.env['res.config.settings'].new()
        settings.set_dropbox_refresh_token(self.dropbox_authorization_code.strip())
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Dropbox'),
                'message': _('Dropbox connected and tokens stored successfully.'),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            }
        }
