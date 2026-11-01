# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, api


def uninstall_hook(env):
    """Delete the System Parameters created by this module."""
    if not isinstance(env, api.Environment):
        env = api.Environment(env, SUPERUSER_ID, {})
    keys = [
        'google_drive_integration.client_id',
        'google_drive_integration.client_secret',
        'google_drive_integration.access_token',
        'google_drive_integration.refresh_token',
        'google_drive_integration.parent_folder_id',
        'google_drive_integration.gdrive_button',
        'google_drive_integration.oauth_state',
    ]
    env['ir.config_parameter'].sudo().search([('key', 'in', keys)]).unlink()
