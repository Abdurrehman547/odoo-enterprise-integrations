# -*- coding: utf-8 -*-
import logging

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google.auth.exceptions import RefreshError
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:
    _logger.debug("Google client libraries not installed.")
    Credentials = None

GOOGLE_DRIVE_SCOPE = ['https://www.googleapis.com/auth/drive.file']
GOOGLE_TOKEN_URI = 'https://oauth2.googleapis.com/token'


class FolderResult(str):
    """String subclass that holds the folder URL while providing .id and dict-like access."""
    def __new__(cls, url, folder_id):
        obj = super().__new__(cls, url or '')
        obj.id = folder_id
        obj.url = url or ''
        return obj

    def get(self, key, default=None):
        if key == 'id':
            return self.id
        if key in ('url', 'webViewLink'):
            return self.url
        return default

    def __getitem__(self, item):
        if item == 'id':
            return self.id
        if item in ('url', 'webViewLink'):
            return self.url
        return super().__getitem__(item)


class GoogleDriveService(models.AbstractModel):
    """Shared helper that authenticates against Google Drive and creates
    folders. Used by res.partner and the folder wizard."""
    _name = 'google.drive.service'
    _description = 'Google Drive Service'

    @api.model
    def _get_param(self, key):
        return self.env['ir.config_parameter'].sudo().get_param(
            'google_drive_integration.%s' % key)

    @api.model
    def _get_credentials(self):
        """Build a Credentials object from stored tokens and refresh when expired."""
        if Credentials is None:
            raise UserError(_(
                "The Python libraries 'google-api-python-client' and "
                "'google-auth' are required. Please install them on the server."))

        client_id = self._get_param('client_id')
        client_secret = self._get_param('client_secret')
        refresh_token = self._get_param('refresh_token')
        access_token = self._get_param('access_token')

        if not (client_id and client_secret and refresh_token):
            raise UserError(_(
                "Google Drive is not connected. Open Settings > General Settings "
                "and click 'Connect to Google Drive' first."))

        creds = Credentials(
            token=access_token or None,
            refresh_token=refresh_token,
            token_uri=GOOGLE_TOKEN_URI,
            client_id=client_id,
            client_secret=client_secret,
            scopes=GOOGLE_DRIVE_SCOPE,
        )

        if not creds.valid:
            try:
                creds.refresh(Request())
                self.env['ir.config_parameter'].sudo().set_param(
                    'google_drive_integration.access_token', creds.token)
            except RefreshError as err:
                _logger.warning("Google Drive token refresh failed: %s", err)
                raise UserError(_(
                    "Your Google Drive authorization has expired or was revoked. "
                    "Please reconnect in Settings.\n\n(%s)") % err)

        return creds

    @api.model
    def _get_drive_service(self):
        creds = self._get_credentials()
        return build('drive', 'v3', credentials=creds, cache_discovery=False)

    @api.model
    def create_folder(self, name):
        """Create a folder under the configured parent, make it viewable by
        anyone with the link, and return the folder's webViewLink."""
        if not name or not name.strip():
            raise UserError(_("Please provide a valid folder name."))

        service = self._get_drive_service()
        parent_id = self._get_param('parent_folder_id')

        metadata = {
            'name': name.strip(),
            'mimeType': 'application/vnd.google-apps.folder',
        }
        if parent_id and parent_id.strip():
            metadata['parents'] = [parent_id.strip()]

        try:
            folder = service.files().create(
                body=metadata,
                fields='id, webViewLink',
                supportsAllDrives=True,
            ).execute()

            # Set public reader permissions (viewable by anyone with link)
            service.permissions().create(
                fileId=folder['id'],
                body={'role': 'reader', 'type': 'anyone'},
                supportsAllDrives=True,
            ).execute()
        except HttpError as err:
            _logger.exception("Google Drive API error during folder creation")
            raise UserError(_("Google Drive API Error: %s") % err)

        return FolderResult(folder.get('webViewLink'), folder.get('id'))

    @api.model
    def extract_folder_id_from_url(self, url):
        """Extract Google Drive folder ID from common URL formats."""
        if not url:
            return False
        import re
        m = re.search(r'/folders/([a-zA-Z0-9_-]+)', url)
        if m:
            return m.group(1)
        m = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', url)
        if m:
            return m.group(1)
        m = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
        if m:
            return m.group(1)
        return False

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
    def upload_file(self, folder_id, file_name, file_content, mimetype=None):
        """Upload raw file bytes into a Google Drive folder.

        :param folder_id: ID of the Google Drive folder.
        :param file_name: Name of the file with extension.
        :param file_content: Bytes of the file.
        :param mimetype: MIME type string.
        :return: dict with 'id', 'name', 'webViewLink'.
        """
        if not folder_id:
            raise UserError(_("Google Drive folder ID is missing."))
        if not file_name or not file_name.strip():
            raise UserError(_("Please provide a valid file name."))
        if not file_content:
            raise UserError(_("Cannot upload an empty file."))

        service = self._get_drive_service()
        mimetype = mimetype or 'application/octet-stream'

        try:
            from googleapiclient.http import MediaIoBaseUpload
            import io
            media = MediaIoBaseUpload(io.BytesIO(file_content), mimetype=mimetype, resumable=True)
            file_metadata = {
                'name': file_name.strip(),
                'parents': [folder_id.strip()],
            }
            uploaded = service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, name, webViewLink',
                supportsAllDrives=True,
            ).execute()

            # Grant public reader permission to the uploaded file
            try:
                service.permissions().create(
                    fileId=uploaded['id'],
                    body={'role': 'reader', 'type': 'anyone'},
                    supportsAllDrives=True,
                ).execute()
            except Exception as perm_err:
                _logger.debug("Could not set reader permission on uploaded file %s: %s", uploaded.get('id'), perm_err)

            return uploaded
        except HttpError as err:
            _logger.exception("Google Drive API error during file upload")
            raise UserError(_("Google Drive API Error during upload: %s") % err)

    @api.model
    def upload_attachment(self, folder_id, attachment):
        """Upload an ir.attachment record to the specified Google Drive folder."""
        file_bytes = self._get_attachment_bytes(attachment)
        return self.upload_file(
            folder_id=folder_id,
            file_name=attachment.name or 'attachment',
            file_content=file_bytes,
            mimetype=attachment.mimetype or 'application/octet-stream',
        )
