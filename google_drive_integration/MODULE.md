# MODULE.md — google_drive_integration

> Routing index entry. Read this file before touching Google Drive API integrations, `res.partner`, `res.config.settings`, or OAuth callback controllers.

## Module Overview

**Google Drive Integration** (`google_drive_integration`, Index World, v19.0.1.0.0, AGPL-3) is a standalone, generic Odoo 19 module providing full Google Drive cloud storage integration. Using the standard OAuth 2.0 Web Application flow, it authorizes Odoo against the Google Drive REST API (v3) under the secure `drive.file` scope.

The module provides administrative setup in General Settings, CSRF-protected web authorization, automatic token management, and generic folder creation on contacts/partners (`res.partner`) and records. Folders are automatically configured with public "reader" permissions, and the shareable link is stored on records, exposed across form headers, smart buttons, URL widgets, and Kanban cards.

---

## Data Models & Schema

### NEW Models

#### 1. `google.drive.service` (`models.AbstractModel`)
- **Description:** Google Drive Service
- **Purpose:** Stateless utility model providing shared OAuth credential management, token refresh handling, and Google Drive REST API v3 operations.
- **Fields:** None (AbstractModel).

#### 2. `google.drive.folder.wizard` (`models.TransientModel`)
- **Description:** Google Drive Folder Creation Wizard
- **Fields:**
  - `name` (`Char`, required=True, string="Folder Name", help="Name of the folder to create in Google Drive")
  - `res_model` (`Char`, string="Resource Model")
  - `res_id` (`Integer`, string="Resource ID")
  - `partner_id` (`Many2one` → `res.partner`, string="Partner")

#### 3. `google.drive.upload.wizard` (`models.TransientModel`)
- **Description:** Upload Attachments to Google Drive Wizard
- **Fields:**
  - `partner_id` (`Many2one` → `res.partner`, string="Contact", readonly=True)
  - `res_model` (`Char`, string="Resource Model")
  - `res_id` (`Integer`, string="Resource ID")
  - `gdrive_folder_link` (`Char`, string="Google Drive Folder", readonly=True)
  - `attachment_ids` (`Many2many` → `ir.attachment`, string="Attachments to Upload")
  - `has_existing_attachments` (`Boolean`, compute)
  - `upload_new_file` (`Boolean`, string="Upload a New File")
  - `file_data` (`Binary`, string="File")
  - `file_name` (`Char`, string="File Name")

---

### Core Models Extended

#### 1. `res.config.settings` (`models.TransientModel`)
Configures Google OAuth 2.0 application credentials and destination Drive folder:
- `gdrive_button` (`Boolean`, config_parameter=`google_drive_integration.gdrive_button`, default=False)
- `gdrive_client_id` (`Char`, copy=False, config_parameter=`google_drive_integration.client_id`, string="Google Client ID")
- `gdrive_client_secret` (`Char`, copy=False, config_parameter=`google_drive_integration.client_secret`, string="Google Client Secret")
- `gdrive_parent_folder_id` (`Char`, config_parameter=`google_drive_integration.parent_folder_id`, string="Parent Folder ID")
- `gdrive_access_token` (`Char`, config_parameter=`google_drive_integration.access_token`, string="Access Token")
- `gdrive_refresh_token` (`Char`, config_parameter=`google_drive_integration.refresh_token`, string="Refresh Token")
- `gdrive_auto_upload` (`Boolean`, config_parameter=`google_drive_integration.auto_upload`, string="Auto-upload Attachments")

#### 2. `res.partner` (`models.Model`)
Stores folder link and provides actions:
- `gdrive_folder_link` (`Char`, string="Google Drive Folder Link")
- `gdrive_folder_id` (`Char`, string="Google Drive Folder ID")
- Actions: `action_create_gdrive_wizard()`, `action_upload_gdrive_wizard()`, `action_open_gdrive_link()`, `_get_gdrive_folder_id()`.

#### 3. `ir.attachment` (`models.Model`)
- `gdrive_file_id` (`Char`, readonly=True, copy=False)
- `gdrive_file_link` (`Char`, readonly=True, copy=False)
- Actions: `action_upload_to_gdrive()`, `create()` (with optional automatic background upload).

---

## Business Logic & Overrides

### 1. `models/google_drive_service.py` (`google.drive.service`)
- **OAuth Scope:** `['https://www.googleapis.com/auth/drive.file']`
- **Token Endpoint:** `https://oauth2.googleapis.com/token`
- `_get_param(key)`: Helper retrieving system parameters under the `google_drive_integration.` namespace with `sudo()`.
- `_get_credentials()`:
  - Validates installation of Python libraries `google-api-python-client` and `google-auth`.
  - Reads `client_id`, `client_secret`, `refresh_token`, and `access_token` from `ir.config_parameter`.
  - Instantiates `google.oauth2.credentials.Credentials`.
  - Checks `creds.valid`; refreshes token only when expired via `creds.refresh(Request())` and persists fresh `access_token` back to `ir.config_parameter`.
  - Traps `RefreshError` and converts to user-friendly `UserError` instructing the admin to reconnect.
- `_get_drive_service()`:
  - Invokes `build('drive', 'v3', credentials=creds, cache_discovery=False)`.
- `create_folder(name)`:
  - Accepts a folder name string.
  - Builds folder metadata with MIME type `application/vnd.google-apps.folder`. Injects `parents=[parent_id]` if `parent_folder_id` is configured.
  - Executes `service.files().create(body=metadata, fields='id, webViewLink', supportsAllDrives=True)`.
  - Automatically creates reader permission: `service.permissions().create(fileId=folder['id'], body={'role': 'reader', 'type': 'anyone'}, supportsAllDrives=True)`.
  - Returns `folder.get('webViewLink')`. Traps `HttpError` into `UserError`.

### 2. `models/res_config_settings.py` (`res.config.settings`)
- `_get_redirect_uri()`: Computes `{web.base.url}/google_drive/callback`.
- `action_connect_google_drive()`:
  - Persists current values of `gdrive_client_id`, `gdrive_client_secret`, and `gdrive_parent_folder_id` to `ir.config_parameter`.
  - Validates presence of client ID and secret (`UserError` if missing).
  - Generates random CSRF state (`uuid.uuid4().hex`) and saves to `google_drive_integration.oauth_state`.
  - Dispatches browser redirect (`ir.actions.act_url`, target `self`) to `https://accounts.google.com/o/oauth2/v2/auth` with `response_type=code`, `access_type=offline`, `prompt=consent`, and `include_granted_scopes=true`.

### 3. `models/res_partner.py` (`res.partner`)
- `action_open_gdrive_link()`: Returns `ir.actions.act_url` opening `gdrive_folder_link` in a new browser tab (`target='new'`).
- `action_create_gdrive_wizard()`: Returns `ir.actions.act_window` launching wizard `google.drive.folder.wizard` in target `new` with context `default_partner_id=self.id`.

### 4. `models/gdrive_wizard.py` (`google.drive.folder.wizard`)
- **Generic Architecture:** Accepts any active record via `active_model` and `active_id`.
- `default_get(fields_list)`: Automatically initializes target model, resource ID, and default folder name from the active record or partner.
- `create_gdrive_folder()`: Calls `google.drive.service.create_folder(self.name)`. Updates `record.gdrive_folder_link` on the active resource and/or `partner_id.gdrive_folder_link`, returning a non-blocking toast notification.

### 5. `hooks.py` (`uninstall_hook`)
- Cleans up all 7 system parameters (`client_id`, `client_secret`, `access_token`, `refresh_token`, `parent_folder_id`, `gdrive_button`, `oauth_state`) from `ir.config_parameter` upon uninstallation.

---

## UI & Views

### 1. `views/res_config_settings_views.xml`
- Inherits `base_setup.res_config_settings_view_form`.
- Injects inside `//block[@name='integration']`:
  - Toggle box for `gdrive_button` ("Google Drive").
  - Nested group (invisible when `not gdrive_button`) with fields:
    - `gdrive_client_id`
    - `gdrive_client_secret` (masked with `password="True"`)
    - `gdrive_parent_folder_id`
    - `gdrive_refresh_token` (`readonly="1"`)
    - Action button `action_connect_google_drive` ("Connect to Google Drive", `icon="fa-google"`).

### 2. `views/res_partner.xml`
- Inherits `base.view_partner_form`:
  - `//form/sheet` position `before`: Adds `<header>` containing primary action button `action_create_gdrive_wizard` ("Create Google Drive Folder", `oe_highlight`, `invisible="gdrive_folder_link"`).
  - `//field[@name='vat']` position `after`: Adds `gdrive_folder_link` (`widget="url"`, `readonly="1"`).
  - `//div[@name='button_box']` position `inside`: Adds stat button `action_open_gdrive_link` with icon `fa-google` and label "Google Drive" (`invisible="not gdrive_folder_link"`).
- Inherits `base.res_partner_kanban_view`:
  - `//footer` position `inside`: Adds clickable icon `action_open_gdrive_link` (`icon="fa-google"`, title="Google Drive", `invisible="not gdrive_folder_link"`).

### 3. `views/gdrive_wizard.xml`
- View: `view_google_drive_folder_wizard_form` (`google.drive.folder.wizard`):
  - Modal form containing `name` (required, placeholder "Enter folder name"), hidden `res_model`, `res_id`, and `partner_id`.
  - Footer buttons: `create_gdrive_folder` (class `btn-primary`, hotkey `q`) and Cancel (class `btn-secondary`, `special="cancel"`, hotkey `z`).

---

## Endpoints, Reports & Services

### HTTP Controllers (`controllers/main.py`)
- `GoogleDriveController(http.Controller)`:
  - Route: `/google_drive/callback`
  - Protocol: `type='http'`, `auth='user'`, `methods=['GET']`, `csrf=False`.
  - Workflow:
    1. Checks for error query params.
    2. Validates `state` against `google_drive_integration.oauth_state` (CSRF protection) and clears the state.
    3. Retrieves `client_id`, `client_secret`, and redirect URI from config parameters.
    4. Issues POST request to `https://oauth2.googleapis.com/token` (`grant_type='authorization_code'`).
    5. Saves `access_token` and `refresh_token` in `ir.config_parameter`.
    6. Calls `_render_message()` returning a styled responsive confirmation/error HTML page with a button redirecting back to `/odoo/settings`.

---

## Security & Access

### Access Rights (`security/ir.model.access.csv`)
- `access_google_drive_folder_wizard`: Gives `base.group_user` (Internal Users) full read, write, create, and unlink permissions on `google.drive.folder.wizard`.
- `access_google_drive_upload_wizard`: Gives `base.group_user` (Internal Users) full read, write, create, and unlink permissions on `google.drive.upload.wizard`.
- `google.drive.service` is an `AbstractModel` (no database table, no ACL required).

---

## Dependencies & Load Order

### Upstream Dependencies (`__manifest__.py`)
- `base`
- `base_setup`

### Python External Dependencies
- `google-api-python-client`
- `requests`

### Manifest Data Sequence
1. `security/ir.model.access.csv`
2. `views/res_config_settings_views.xml`
3. `views/res_partner.xml`
4. `views/gdrive_wizard.xml`
5. `views/gdrive_upload_wizard_views.xml`
6. `views/ir_attachment_views.xml`

### Hooks
- `uninstall_hook`: `uninstall_hook` in `hooks.py`.
