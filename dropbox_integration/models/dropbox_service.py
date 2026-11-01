# -*- coding: utf-8 -*-
import logging

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    import dropbox
    from dropbox.exceptions import ApiError, AuthError
except ImportError:
    dropbox = None
    ApiError = None
    AuthError = None


class DropboxService(models.AbstractModel):
    _name = 'dropbox.service'
    _description = 'Dropbox Service'

    @api.model
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

    @api.model
    def upload_file(self, folder_path, file_name, file_content):
        """Upload raw bytes to a Dropbox folder path.

        :param folder_path: Target directory path in Dropbox (e.g. '/Odoo/Douglas Fletcher')
        :param file_name: File name with extension
        :param file_content: Bytes of the file
        :return: dict with 'id', 'name', 'path_lower', 'url'
        """
        if not folder_path:
            raise UserError(_("Dropbox folder path is missing."))
        if not file_name or not file_name.strip():
            raise UserError(_("Please provide a valid file name."))
        if not file_content:
            raise UserError(_("Cannot upload an empty file."))

        dbx = self._get_dropbox_client()
        clean_folder = folder_path.strip().rstrip('/')
        clean_file = file_name.strip().lstrip('/')
        file_path = f"{clean_folder}/{clean_file}"

        try:
            meta = dbx.files_upload(
                file_content,
                file_path,
                mode=dropbox.files.WriteMode.overwrite
            )
            # Retrieve or create shareable link
            file_url = None
            try:
                link_meta = dbx.sharing_create_shared_link_with_settings(meta.path_lower)
                file_url = link_meta.url
            except ApiError as err:
                if err.error.is_shared_link_already_exists():
                    links = dbx.sharing_list_shared_links(path=meta.path_lower, direct_only=True).links
                    file_url = links[0].url if links else None
                else:
                    _logger.debug("Could not generate shared link for %s: %s", file_path, err)

            return {
                'id': meta.id,
                'name': meta.name,
                'path_lower': meta.path_lower,
                'url': file_url or '',
            }
        except AuthError as err:
            _logger.exception("Dropbox authentication error during upload")
            raise UserError(_("Dropbox authorization expired or revoked. Please reconnect in Settings.\n\n(%s)") % err)
        except Exception as e:
            _logger.exception("Dropbox upload error")
            raise UserError(_("Dropbox Upload Error: %s") % e)

    @api.model
    def _get_attachment_bytes(self, attachment):
        """Extract raw bytes from an ir.attachment record."""
        if hasattr(attachment, 'raw') and attachment.raw:
            return attachment.raw
        if attachment.datas:
            import base64
            return base64.b64decode(attachment.datas)
        return b''

    @api.model
    def upload_attachment(self, folder_path, attachment):
        """Upload an ir.attachment to the specified Dropbox folder."""
        file_bytes = self._get_attachment_bytes(attachment)
        return self.upload_file(
            folder_path=folder_path,
            file_name=attachment.name or 'attachment',
            file_content=file_bytes,
        )
