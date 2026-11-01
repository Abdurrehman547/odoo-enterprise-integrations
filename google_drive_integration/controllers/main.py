# -*- coding: utf-8 -*-
import logging

import requests

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URI = 'https://oauth2.googleapis.com/token'


class GoogleDriveController(http.Controller):

    @http.route('/google_drive/callback', type='http', auth='user',
                methods=['GET'], csrf=False)
    def google_drive_callback(self, **kw):
        """Receive Google's authorization code, exchange it for tokens and
        store them. Then redirect back to settings."""
        ICP = request.env['ir.config_parameter'].sudo()

        if kw.get('error'):
            return self._render_message(
                "Authorization was cancelled or failed: %s" % kw.get('error'),
                success=False)

        # Verify CSRF state
        expected_state = ICP.get_param('google_drive_integration.oauth_state')
        if not kw.get('state') or kw.get('state') != expected_state:
            return self._render_message(
                "Invalid state parameter. Please try connecting again.",
                success=False)
        ICP.set_param('google_drive_integration.oauth_state', '')

        code = kw.get('code')
        if not code:
            return self._render_message(
                "No authorization code received from Google.", success=False)

        client_id = ICP.get_param('google_drive_integration.client_id')
        client_secret = ICP.get_param('google_drive_integration.client_secret')
        base_url = ICP.get_param('web.base.url').rstrip('/')
        redirect_uri = base_url + '/google_drive/callback'

        try:
            resp = requests.post(GOOGLE_TOKEN_URI, data={
                'code': code,
                'client_id': client_id,
                'client_secret': client_secret,
                'redirect_uri': redirect_uri,
                'grant_type': 'authorization_code',
            }, timeout=15)
        except requests.RequestException as err:
            _logger.exception("Google token exchange request failed")
            return self._render_message(
                "Could not reach Google: %s" % err, success=False)

        if resp.status_code != 200:
            _logger.warning("Google token exchange failed: %s", resp.text)
            return self._render_message(
                "Token exchange failed: %s" % resp.text, success=False)

        tokens = resp.json()
        access_token = tokens.get('access_token')
        refresh_token = tokens.get('refresh_token')

        if access_token:
            ICP.set_param('google_drive_integration.access_token', access_token)
        if refresh_token:
            ICP.set_param('google_drive_integration.refresh_token', refresh_token)

        if not refresh_token and not ICP.get_param('google_drive_integration.refresh_token'):
            return self._render_message(
                "Connected, but Google did not return a refresh token. "
                "Please disconnect access in your Google Account security settings and reconnect "
                "to prompt for offline access consent.", success=False)

        return self._render_message(
            "Google Drive connected successfully.", success=True)

    def _render_message(self, message, success=True):
        color = '#28a745' if success else '#dc3545'
        title = 'Success' if success else 'Connection Issue'
        html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>Google Drive Integration</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; text-align: center; margin-top: 80px; background: #f8f9fa; }
        .card { background: white; max-width: 500px; margin: auto; padding: 40px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        h2 { margin-bottom: 16px; }
        p { color: #555; line-height: 1.5; }
        .btn { display: inline-block; margin-top: 24px; padding: 12px 24px; background: #714B67; color: #fff; text-decoration: none; border-radius: 4px; font-weight: 500; }
        .btn:hover { background: #5a3c52; }
    </style>
</head>
<body>
    <div class="card">
        <h2 style="color: %s;">%s</h2>
        <p>%s</p>
        <a href="/odoo/settings" class="btn">Back to Settings</a>
    </div>
</body>
</html>""" % (color, title, message)
        return request.make_response(html)
