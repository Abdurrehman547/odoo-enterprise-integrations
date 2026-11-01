# -*- coding: utf-8 -*-
import logging
from odoo import _, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

try:
    import dropbox
except ImportError:
    _logger.debug("Dropbox Python SDK not installed.")
    dropbox = None


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    dropbox_button = fields.Boolean(
        string='Dropbox',
        config_parameter='dropbox_integration.dropbox_button',
        default=False,
        help="Enable Dropbox integration"
    )
    dropbox_client_id = fields.Char(
        string='Dropbox App Key',
        copy=False,
        config_parameter='dropbox_integration.client_id',
        help="App Key from Dropbox App Console"
    )
    dropbox_client_secret = fields.Char(
        string='Dropbox App Secret',
        copy=False,
        config_parameter='dropbox_integration.client_secret',
        help="App Secret from Dropbox App Console"
    )
    dropbox_access_token = fields.Char(
        string='Dropbox Access Token',
        config_parameter='dropbox_integration.access_token',
        help="Short-lived token refreshed automatically"
    )
    dropbox_refresh_token = fields.Char(
        string='Dropbox Refresh Token',
        config_parameter='dropbox_integration.refresh_token',
        help="Long-lived token for refreshing access"
    )
    dropbox_folder_path = fields.Char(
        string='Dropbox Root Folder Path',
        config_parameter='dropbox_integration.folder_path',
        help="Base directory path in Dropbox under which new folders are created (e.g. /Odoo)"
    )
    dropbox_auto_upload = fields.Boolean(
        string='Dropbox Auto-upload Attachments',
        config_parameter='dropbox_integration.auto_upload',
        default=False,
        help="Automatically upload new attachments created on contacts to their linked Dropbox folder."
    )

    def action_get_dropbox_auth_code(self):
        """Open wizard to set up Dropbox Authorization code."""
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('dropbox_integration.client_id', self.dropbox_client_id or '')
        ICP.set_param('dropbox_integration.client_secret', self.dropbox_client_secret or '')
        ICP.set_param('dropbox_integration.folder_path', self.dropbox_folder_path or '')

        if not (self.dropbox_client_id and self.dropbox_client_secret):
            raise UserError(_("Please provide both App Key and App Secret before connecting."))

        return {
            'type': 'ir.actions.act_window',
            'name': 'Dropbox Authorization Wizard',
            'res_model': 'authentication.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_config_id': self.id,
                'dropbox_auth': True,
            }
        }

    def get_dropbox_auth_url(self):
        """Return Dropbox OAuth 2.0 authorization URL."""
        if not dropbox:
            raise UserError(_("The 'dropbox' Python library is required. Please install it on the server."))

        client_id = self.env['ir.config_parameter'].sudo().get_param('dropbox_integration.client_id')
        client_secret = self.env['ir.config_parameter'].sudo().get_param('dropbox_integration.client_secret')

        if not (client_id and client_secret):
            raise UserError(_("Please configure Dropbox App Key and App Secret first."))

        dbx_auth = dropbox.oauth.DropboxOAuth2FlowNoRedirect(
            client_id,
            client_secret,
            token_access_type='offline'
        )
        return dbx_auth.start()

    def set_dropbox_refresh_token(self, auth_code):
        """Exchange authorization code for refresh & access tokens."""
        if not dropbox:
            raise UserError(_("The 'dropbox' Python library is required. Please install it on the server."))

        try:
            client_id = self.env['ir.config_parameter'].sudo().get_param('dropbox_integration.client_id')
            client_secret = self.env['ir.config_parameter'].sudo().get_param('dropbox_integration.client_secret')

            dbx_auth = dropbox.oauth.DropboxOAuth2FlowNoRedirect(
                client_id,
                client_secret,
                token_access_type='offline'
            )
            oauth_result = dbx_auth.finish(auth_code.strip())

            ICP = self.env['ir.config_parameter'].sudo()
            if oauth_result.refresh_token:
                ICP.set_param('dropbox_integration.refresh_token', oauth_result.refresh_token)
            if oauth_result.access_token:
                ICP.set_param('dropbox_integration.access_token', oauth_result.access_token)

        except Exception as e:
            _logger.exception("Failed to connect with Dropbox")
            raise ValidationError(_("Failed to connect with Dropbox: %s") % e)
