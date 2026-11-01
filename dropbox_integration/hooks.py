# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, api


def uninstall_hook(env):
    """Delete System Parameters created by this module."""
    if not isinstance(env, api.Environment):
        env = api.Environment(env, SUPERUSER_ID, {})
    keys = [
        'dropbox_integration.client_id',
        'dropbox_integration.client_secret',
        'dropbox_integration.access_token',
        'dropbox_integration.refresh_token',
        'dropbox_integration.folder_path',
        'dropbox_integration.dropbox_button',
    ]
    env['ir.config_parameter'].sudo().search([('key', 'in', keys)]).unlink()
