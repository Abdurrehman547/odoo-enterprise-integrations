# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    import dropbox
    from dropbox.exceptions import ApiError, AuthError
except ImportError:
    dropbox = None


class DropboxFolderWizard(models.TransientModel):
    _name = 'dropbox.folder.wizard'
    _description = 'Dropbox Folder Creation Wizard'

    name = fields.Char(
        string='Folder Name',
        required=True,
        help="Name of the folder to create in Dropbox"
    )
    res_model = fields.Char(string='Resource Model')
    res_id = fields.Integer(string='Resource ID')
    partner_id = fields.Many2one('res.partner', string='Partner')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self.env.context
        active_model = ctx.get('active_model')
        active_id = ctx.get('active_id')

        if active_model and active_id:
            res.setdefault('res_model', active_model)
            res.setdefault('res_id', active_id)

            if active_model == 'res.partner':
                res.setdefault('partner_id', active_id)
                partner = self.env['res.partner'].browse(active_id)
                res.setdefault('name', partner.name)
            elif active_model in self.env:
                record = self.env[active_model].browse(active_id)
                if hasattr(record, 'partner_id') and record.partner_id:
                    res.setdefault('partner_id', record.partner_id.id)
                if hasattr(record, 'display_name') and record.display_name:
                    res.setdefault('name', record.display_name)
                elif hasattr(record, 'name') and record.name:
                    res.setdefault('name', record.name)
        return res

    def _get_dropbox_client(self):
        """Build Dropbox client with automatic refresh token support."""
        if not dropbox:
            raise UserError(_("The 'dropbox' Python library is required. Please install it on the server."))

        ICP = self.env['ir.config_parameter'].sudo()
        app_key = ICP.get_param('dropbox_integration.client_id')
        app_secret = ICP.get_param('dropbox_integration.client_secret')
        refresh_token = ICP.get_param('dropbox_integration.refresh_token')
        access_token = ICP.get_param('dropbox_integration.access_token')

        if not (refresh_token or access_token):
            raise UserError(_("Dropbox is not connected. Open Settings > General Settings and connect first."))

        if refresh_token and app_key and app_secret:
            return dropbox.Dropbox(
                oauth2_refresh_token=refresh_token,
                app_key=app_key,
                app_secret=app_secret
            )
        elif access_token:
            return dropbox.Dropbox(access_token)
        else:
            raise UserError(_("Dropbox credentials are incomplete. Please reconnect in Settings."))

    def create_dropbox_folder(self):
        """Create a folder in Dropbox and return its shareable URL."""
        self.ensure_one()
        if not self.name or not self.name.strip():
            raise UserError(_("Please provide a valid folder name."))

        dbx = self._get_dropbox_client()
        ICP = self.env['ir.config_parameter'].sudo()
        root_path = (ICP.get_param('dropbox_integration.folder_path') or '').strip().strip('/')
        folder_name = self.name.strip().strip('/')

        full_path = f"/{root_path}/{folder_name}" if root_path else f"/{folder_name}"

        try:
            # Step 1: Create the Folder
            try:
                dbx.files_create_folder_v2(full_path)
            except ApiError as err:
                # If path conflict, continue to get/create shared link
                if not err.error.is_path() or not err.error.get_path().is_conflict():
                    raise

            # Step 2: Create or retrieve Shared Link
            try:
                shared_link_metadata = dbx.sharing_create_shared_link_with_settings(full_path)
                folder_url = shared_link_metadata.url
            except ApiError as err:
                if err.error.is_shared_link_already_exists():
                    links = dbx.sharing_list_shared_links(path=full_path, direct_only=True).links
                    folder_url = links[0].url if links else None
                else:
                    raise

            if not folder_url:
                raise UserError(_("Failed to retrieve shared link for folder."))

            # Direct web URL format
            folder_url = folder_url.replace("?dl=0", "?raw=1")

            # Update active record if it has the link field
            if self.res_model and self.res_id and self.res_model in self.env:
                record = self.env[self.res_model].browse(self.res_id)
                if hasattr(record, 'dropbox_folder_link'):
                    record.dropbox_folder_link = folder_url
                if hasattr(record, 'dropbox_folder_path'):
                    record.dropbox_folder_path = full_path
                if not self.partner_id and hasattr(record, 'partner_id') and record.partner_id:
                    record.partner_id.dropbox_folder_link = folder_url
                    record.partner_id.dropbox_folder_path = full_path

            # Update partner if set
            if self.partner_id:
                self.partner_id.dropbox_folder_link = folder_url
                self.partner_id.dropbox_folder_path = full_path

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Dropbox'),
                    'message': _('Folder created and link attached successfully.'),
                    'type': 'success',
                    'sticky': False,
                    'next': {'type': 'ir.actions.act_window_close'},
                }
            }

        except AuthError as err:
            _logger.exception("Dropbox authentication error")
            raise UserError(_("Dropbox authorization expired or revoked. Please reconnect in Settings.\n\n(%s)") % err)
        except Exception as e:
            _logger.exception("Dropbox folder creation error")
            raise UserError(_("Dropbox Error: %s") % e)
