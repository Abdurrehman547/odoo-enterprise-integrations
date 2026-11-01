# MODULE.md — dropbox_integration

> Routing index entry. Read this file before modifying Dropbox API integrations, `res.partner`, `res.config.settings`, or Dropbox folder wizards.

## Module Overview

**Dropbox Integration** (`dropbox_integration`, Index World, v19.0.1.0.0, AGPL-3) provides direct cloud storage integration between Odoo 19 and Dropbox. Utilizing Dropbox's OAuth 2.0 PKCE / Offline token flow (`DropboxOAuth2FlowNoRedirect`), it enables users to configure credentials, authenticate via authorization codes, and automatically create and link dedicated Dropbox folders on Contacts/Partners (`res.partner`) and records.

Created folders are assigned public shared link access (`sharing_create_shared_link_with_settings`), and the direct web link is saved to `dropbox_folder_link`, accessible via smart stat buttons, header buttons, URL widgets, and Kanban cards.

---

## Data Models & Schema

### NEW Models

#### 1. `dropbox.folder.wizard` (`models.TransientModel`)
- **Description:** Dropbox Folder Creation Wizard
- **Fields:**
  - `name` (`Char`, required=True, string="Folder Name", help="Name of the folder to create in Dropbox")
  - `res_model` (`Char`, string="Resource Model")
  - `res_id` (`Integer`, string="Resource ID")
  - `partner_id` (`Many2one` → `res.partner`, string="Partner")

#### 2. `authentication.wizard` (`models.TransientModel`)
- **Description:** Authentication Code Wizard
- **Fields:**
  - `dropbox_authorization_code` (`Char`, string="Dropbox Authorization Code")
  - `dropbox_auth_url` (`Char`, compute='_compute_dropbox_auth_url', string="Dropbox Authentication URL")

#### 3. `dropbox.service` (`models.AbstractModel`)
- **Description:** Dropbox Service utility for uploading files and attachments.
- **Methods:** `_get_dropbox_client()`, `upload_file(folder_path, file_name, file_content)`, `upload_attachment(folder_path, attachment)`.

#### 4. `dropbox.upload.wizard` (`models.TransientModel`)
- **Description:** Upload Attachments to Dropbox Wizard
- **Fields:**
  - `partner_id` (`Many2one` → `res.partner`, string="Contact", readonly=True)
  - `res_model` (`Char`, string="Resource Model")
  - `res_id` (`Integer`, string="Resource ID")
  - `dropbox_folder_link` (`Char`, string="Dropbox Folder", readonly=True)
  - `attachment_ids` (`Many2many` → `ir.attachment`, string="Attachments to Upload")
  - `has_existing_attachments` (`Boolean`, compute)
  - `upload_new_file` (`Boolean`, string="Upload a New File")
  - `file_data` (`Binary`, string="File")
  - `file_name` (`Char`, string="File Name")

---

### Core Models Extended

#### 1. `res.config.settings` (`models.TransientModel`)
Configures Dropbox App credentials, tokens, and base directory:
- `dropbox_button` (`Boolean`, config_parameter=`dropbox_integration.dropbox_button`, default=False)
- `dropbox_client_id` (`Char`, copy=False, config_parameter=`dropbox_integration.client_id`, string="Dropbox App Key")
- `dropbox_client_secret` (`Char`, copy=False, config_parameter=`dropbox_integration.client_secret`, string="Dropbox App Secret")
- `dropbox_access_token` (`Char`, config_parameter=`dropbox_integration.access_token`, string="Dropbox Access Token")
- `dropbox_refresh_token` (`Char`, config_parameter=`dropbox_integration.refresh_token`, string="Dropbox Refresh Token")
- `dropbox_folder_path` (`Char`, config_parameter=`dropbox_integration.folder_path`, string="Dropbox Root Folder Path")
- `dropbox_auto_upload` (`Boolean`, config_parameter=`dropbox_integration.auto_upload`, string="Dropbox Auto-upload Attachments")

#### 2. `res.partner` (`models.Model`)
Stores folder link and provides actions:
- `dropbox_folder_link` (`Char`, string="Dropbox Folder Link")
- `dropbox_folder_path` (`Char`, string="Dropbox Folder Path")
- Actions: `action_create_dropbox_wizard()`, `action_upload_dropbox_wizard()`, `action_open_dropbox_link()`, `_get_dropbox_folder_path()`.

#### 3. `ir.attachment` (`models.Model`)
- `dropbox_file_id` (`Char`, readonly=True, copy=False)
- `dropbox_file_link` (`Char`, readonly=True, copy=False)
- Actions: `action_upload_to_dropbox()`, `create()` (with optional automatic background upload).

---

## Business Logic & Overrides

### 1. `models/res_config_settings.py` (`res.config.settings`)
- `action_get_dropbox_auth_code()`: Saves parameters to `ir.config_parameter` and launches `authentication.wizard` modal.
- `get_dropbox_auth_url()`: Instantiates `dropbox.oauth.DropboxOAuth2FlowNoRedirect` with `token_access_type='offline'` and returns the Dropbox consent URL.
- `set_dropbox_refresh_token(auth_code)`: Finishes OAuth exchange with Dropbox, storing `refresh_token` and `access_token` in `ir.config_parameter`.

### 2. `models/res_partner.py` (`res.partner`)
- `action_open_dropbox_link()`: Returns `ir.actions.act_url` opening `dropbox_folder_link` in `target='new'`.
- `action_create_dropbox_wizard()`: Returns `ir.actions.act_window` launching `dropbox.folder.wizard` in target `new`.

### 3. `models/dropbox_wizard.py` (`dropbox.folder.wizard`)
- **Generic Architecture:** Accepts any active record via `active_model` and `active_id`.
- `_get_dropbox_client()`: Returns an authenticated `dropbox.Dropbox` client with auto-refreshing token support (`oauth2_refresh_token`, `app_key`, `app_secret`).
- `create_dropbox_folder()`:
  - Normalizes root and folder paths (`/{root_path}/{folder_name}`).
  - Calls `dbx.files_create_folder_v2()`.
  - Generates or lists shared links via `dbx.sharing_create_shared_link_with_settings()` and formats direct URL (`?raw=1`).
  - Stores URL on the active record and/or `partner_id.dropbox_folder_link`.
  - Dispatches non-blocking toast notification.

### 4. `hooks.py` (`uninstall_hook`)
- Cleans up all 6 stored parameters (`client_id`, `client_secret`, `access_token`, `refresh_token`, `folder_path`, `dropbox_button`) from `ir.config_parameter` upon uninstallation.

---

## UI & Views

### 1. `views/res_config_settings_views.xml`
- Inherits `base_setup.res_config_settings_view_form`.
- Injects inside `//block[@name='integration']`:
  - Toggle box for `dropbox_button` ("Dropbox").
  - Nested group with `dropbox_client_id`, `dropbox_client_secret` (masked), `dropbox_folder_path`, `dropbox_refresh_token` (readonly), and button `action_get_dropbox_auth_code` ("Setup Access Token", icon `fa-dropbox`).

### 2. `views/res_partner.xml`
- Inherits `base.view_partner_form`:
  - Header button `action_create_dropbox_wizard` ("Create Dropbox Folder", `oe_highlight`, `invisible="dropbox_folder_link"`).
  - Field `dropbox_folder_link` (`widget="url"`, `readonly="1"`).
  - Stat button `action_open_dropbox_link` in `button_box` with icon `fa-dropbox`.
- Inherits `base.res_partner_kanban_view`:
  - Footer shortcut link with icon `fa-dropbox`.

### 3. `views/dropbox_wizard.xml`
- View: `view_dropbox_folder_wizard_form` (`dropbox.folder.wizard`):
  - Form dialog with `name`, hidden `res_model`, `res_id`, `partner_id`.
  - Footer buttons: `create_dropbox_folder` (`btn-primary`, hotkey `q`) and Cancel (`btn-secondary`, `special="cancel"`, hotkey `z`).

### 4. `wizard/authentication_wizard_views.xml`
- View: `auth_code_wizard_view_form` (`authentication.wizard`):
  - Guidance alert instructions, clickable authorization URL field, authorization code input, and confirm/cancel buttons.

---

## Endpoints, Reports & Services

- No custom HTTP controllers or cron jobs (tokens refresh automatically via Dropbox SDK).

---

## Security & Access

### Access Rights (`security/ir.model.access.csv`)
- Full read, write, create, unlink for `base.group_user` on:
  - `authentication.wizard`
  - `dropbox.folder.wizard`
  - `dropbox.upload.wizard`
- `dropbox.service` is an `AbstractModel` (no database table, no ACL required).

---

## Dependencies & Load Order

### Upstream Dependencies (`__manifest__.py`)
- `base`
- `base_setup`

### Python External Dependencies
- `dropbox`
- `requests`

### Manifest Data Sequence
1. `security/ir.model.access.csv`
2. `views/res_config_settings_views.xml`
3. `views/res_partner.xml`
4. `views/dropbox_wizard.xml`
5. `views/dropbox_upload_wizard_views.xml`
6. `views/ir_attachment_views.xml`
7. `wizard/authentication_wizard_views.xml`

### Hooks
- `uninstall_hook`: `uninstall_hook` in `hooks.py`.
