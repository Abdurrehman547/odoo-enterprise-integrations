# crm_lead

## Module Overview
BaghouseAmerica lead-capture and ads-conversion integration (v0.1, LGPL-3).
Three concerns: (1) public HTTP endpoint `/create/crm/lead` receiving website
form submissions (WordPress) with UTM/gclid/msclkid/fbclid attribution and
multi-company routing; (2) offline-conversion queues pushing qualified/won
conversions to Microsoft Ads (Bing SOAP) and Meta Ads (CAPI HTTP); (3) a
"No Salesperson Change" restriction on sale orders. Python dep:
`websocket-client`.

## Data Models & Schema
New models:
- `microsoft.ads.conversion.queue`: offline conversion queue. Fields:
  `lead_id` (crm.lead, cascade), `msclkid` (indexed, validated 32-hex),
  `conversion_name`, `conversion_time`, `conversion_value`, `currency_id`,
  `state` (pending/sent/failed/void), `is_backfill`, `auto_send`,
  `error_message`, `payload_sent`, `response_received`, `attempt_count`,
  `last_attempt_at`. SQL constraint `unique(msclkid, conversion_name)`.
- `meta.ads.conversion.queue`: Meta CAPI queue. Fields: `lead_id`, `event_name`
  (default "Purchase"), `event_time`, `value`, `currency`, `fbclid`,
  `status` (pending/sent/failed), `error_message`, `sent_at`. Ordered by
  create_date.
- `microsoft.ads.service` (AbstractModel): OAuth token + Bing SOAP client.

Inherited models:
- `crm.lead`: click-id and tracking fields `ls_gclid`, `ls_utm_source`,
  `ls_utm_medium`, `ls_utm_campaign`, `ls_utm_content`, `ls_msclkid`,
  `ls_fbclid`; computed stored `lead_platform` (callrail/website/email/
  unclassified); upload flags `ms_ads_lead_uploaded`, `ms_ads_won_uploaded`,
  `ms_ads_last_error`, `ms_ads_last_upload_at`, `meta_ads_won_uploaded`,
  `meta_ads_last_error`, `meta_ads_last_upload_at`.
- `res.config.settings`: config-parameter fields under `ads_integration.*`
  (meta_access_token, meta_pixel_id, meta_test_event_code, bing_account_id,
  bing_client_id, bing_client_secret, bing_customer_id, bing_developer_token,
  bing_refresh_token, bing_tenant_id, bing_qualified_goal_name,
  bing_won_goal_name, conversion_failure_emails, auto_conversion_start_date).
- `sale.order`: non-stored computed `is_salesperson_restricted`.

## Business Logic & Overrides
- `crm.lead`:
  - `create` (@api.model_create_multi): archives + tags `@avintivmedia.com`
    test submissions with tag `crm_lead.tag_avintiv_test`; fully defensive.
  - `write`: when `ls_msclkid`/`ls_gclid` set on an opportunity, queues
    Microsoft "qualified" conversion and (if installed) Google Ads conversion;
    skipped when context `is_backfill`.
  - `convert_opportunity` override: after standard conversion, queues the
    Bing qualified-lead conversion (dedup by msclkid+goal, validates click id)
    and sends Meta "Lead" event immediately.
  - `_compute_lead_platform` (@api.depends callrail_call_ids, source_id).
  - `action_push_to_ms_ads_queue`: manual queue push (qualified + won goals).
- `microsoft.ads.conversion.queue`:
  - `_check_msclkid` constraint; `mark_sent` (updates lead flags + chatter),
    `mark_failed` (attempt counter, alert email `_notify_failure`),
    `action_send_now` (immediate send, notification actions),
    `send_pending_conversions` (cron drain, max 5 attempts, aborts on OAuth
    errors), `action_void`, `action_reset_to_pending`, `unlink` blocked
    (use void).
- `microsoft.ads.service`:
  - `_get_access_token`: refresh-token grant against
    `login.microsoftonline.com`, rotates refresh token back into settings.
  - `send_offline_conversion`: builds ApplyOfflineConversions SOAP envelope,
    posts to `campaign.api.bingads.microsoft.com` v13, detects Fault and
    PartialErrors.
- `meta.ads.conversion.queue._send_to_meta`: POSTs Conversions API events to
  `graph.facebook.com/v19.0/<pixel>/events` (fbc from fbclid, optional test
  event code), batch of 10 pending.
- `sale.order._compute_is_salesperson_restricted` (@api.depends_context uid):
  true when user is in group `crm_lead.group_no_salesperson_change`.

## UI & Views
- `views/views.xml`:
  - inherits `crm.crm_lead_view_form` (`view_crm_lead_ls_click_ids`): adds
    "Ad Click IDs" group after the `message_bounce` parent group
    (ls_fbclid, ls_gclid, ls_msclkid).
  - inherits `sale.view_order_form` (`sale_order_form_no_salesperson_edit`):
    adds invisible `is_salesperson_restricted` after `user_id` and makes
    `user_id` readonly when restricted.
  - server action `action_push_to_ms_ads_queue` bound to crm.lead
    (list/form Action menu).
- `views/adds_setting.xml`:
  - inherits `base.res_config_settings_view_form`
    (`res_config_settings_view_form_inherit_ads`): Ads Integration block
    inside General Settings app (Meta, Bing, failure-alert settings).
  - new list/form views for `microsoft.ads.conversion.queue` (statusbar
    header with Send Now / Reset to Pending), window action
    `action_microsoft_ads_conversion_queue`, menu
    `menu_microsoft_ads_conversion_queue` under `crm.crm_menu_config`, and
    bound server actions Send Now / Reset to Pending / Void Records.
- Note: `controllers/test.js` and `models/test.py` are standalone dev/test
  scripts (test.py contains hardcoded Bing credentials — dev artifact, not
  loaded by Odoo; `models/run.py` and `controllers/microsoft_ads_oauth.py`
  are empty placeholders).

## Endpoints, Reports & Services
- Route `POST /create/crm/lead` (type=http, auth=public, csrf=False,
  website=True): validates name/email, picks company (IBA=2, default
  Baghouse=1), get-or-creates res.partner company+contact, resolves/creates
  utm.source/medium/campaign records, cleans gclid/msclkid, creates the lead
  (sudo, forced company). Returns JSON status/lead_id.
- External services: Microsoft OAuth2 token endpoint, Bing Campaign
  Management SOAP v13, Meta Graph API v19.0 events; failure alert emails via
  `mail.mail`.
- Crons (ir.cron):
  - `cron_meta_ads_capi_sender` (views.xml): every 1 min →
    `meta.ads.conversion.queue._send_to_meta()`.
  - `cron_send_bing_offline_conversions` (views.xml): every 10 min →
    `microsoft.ads.conversion.queue.send_pending_conversions()`.
  - `ir_cron_microsoft_ads_conversion_queue` (data/cron_data.xml, noupdate):
    every 1 hour → same method.
- No QWeb reports.

## Security & Access
- `security/security.xml`: hidden group `group_no_salesperson_change`
  ("No Salesperson Change").
- `security/ir.model.access.csv`: full CRUD on
  `microsoft.ads.conversion.queue`, `meta.ads.conversion.queue`, and
  `microsoft.ads.service` for `base.group_system` only.
- Data: `crm.tag` "Avintiv Test" (`tag_avintiv_test`, noupdate).

## Dependencies & Load Order
- depends: base, crm, website, sale; python: websocket-client.
- data order: security/ir.model.access.csv → views/views.xml →
  views/adds_setting.xml → security/security.xml → data/cron_data.xml →
  data/crm_lead_data.xml.
