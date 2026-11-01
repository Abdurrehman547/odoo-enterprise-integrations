# -*- coding: utf-8 -*-
import uuid
from urllib.parse import urlencode

from odoo import _, fields, models
from odoo.exceptions import UserError

from .google_drive_service import GOOGLE_DRIVE_SCOPE

GOOGLE_AUTH_URI = 'https://accounts.google.com/o/oauth2/v2/auth'


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    gdrive_button = fields.Boolean(
        config_parameter='google_drive_integration.gdrive_button',
        default=False, help="Enable Google Drive integration")
    gdrive_client_id = fields.Char(
        string='Google Client ID', copy=False,
        config_parameter='google_drive_integration.client_id',
        help="OAuth Client ID from Google Cloud Console")
    gdrive_client_secret = fields.Char(
        string='Google Client Secret', copy=False,
        config_parameter='google_drive_integration.client_secret',
        help="OAuth Client Secret from Google Cloud Console")
    gdrive_parent_folder_id = fields.Char(
        string='Parent Folder ID',
        config_parameter='google_drive_integration.parent_folder_id',
        help="ID of the Drive folder new folders are created under. "
             "Found in the folder URL: drive.google.com/drive/folders/<ID>")
    gdrive_access_token = fields.Char(
        string='Access Token',
        config_parameter='google_drive_integration.access_token',
        help="Short-lived token, refreshed automatically")
    gdrive_refresh_token = fields.Char(
        string='Refresh Token',
        config_parameter='google_drive_integration.refresh_token',
        help="Long-lived token, obtained once when connecting")
    gdrive_auto_upload = fields.Boolean(
        string='Google Drive Auto-upload Attachments',
        config_parameter='google_drive_integration.auto_upload',
        default=False,
        help="Automatically upload new attachments created on contacts to their linked Google Drive folder.")

    def _get_redirect_uri(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param(
            'web.base.url')
        return base_url.rstrip('/') + '/google_drive/callback'

    def action_connect_google_drive(self):
        """Persist the credentials and redirect the browser to Google's
        consent screen. Google calls back /google_drive/callback."""
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        # Persist so the callback controller can exchange the code.
        ICP.set_param('google_drive_integration.client_id',
                      self.gdrive_client_id or '')
        ICP.set_param('google_drive_integration.client_secret',
                      self.gdrive_client_secret or '')
        ICP.set_param('google_drive_integration.parent_folder_id',
                      self.gdrive_parent_folder_id or '')

        if not (self.gdrive_client_id and self.gdrive_client_secret):
            raise UserError(_(
                "Please set the Client ID and Client Secret first."))

        # CSRF protection: store a one-time state, verified in the callback.
        state = uuid.uuid4().hex
        ICP.set_param('google_drive_integration.oauth_state', state)

        params = {
            'client_id': self.gdrive_client_id,
            'redirect_uri': self._get_redirect_uri(),
            'response_type': 'code',
            'scope': ' '.join(GOOGLE_DRIVE_SCOPE),
            'access_type': 'offline',
            'prompt': 'consent',
            'include_granted_scopes': 'true',
            'state': state,
        }
        return {
            'type': 'ir.actions.act_url',
            'url': '%s?%s' % (GOOGLE_AUTH_URI, urlencode(params)),
            'target': 'self',
        }
