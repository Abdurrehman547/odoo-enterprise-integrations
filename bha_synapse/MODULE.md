# bha_synapse

## Module Overview
Synapse Anywhere ERP/WMS integration (BB Logic, version 18.0.0.3). Bi-directional REST integration between Odoo and the Synapse WMS (Freeport): outbound sale-order export (create/update/cancel/resync), inbound purchase-order export (warehouse receipts), inventory snapshot import + reconciliation, shipment import with tracking and automatic delivery validation, attachment upload/download (packing slips, shipping labels), a durable job queue with retry/reconciliation, dashboards, logs, and several daily email reports.

## Data Models & Schema
New models:
- `synapse.connection` — API config: `base_url`, `auth_type` (bearer/apikey/session), `token`, `username`/`password`/`session_cookie`, `custid`, `facility`, order defaults (`default_order_type`, `default_priority`, `default_uom`, `from_facility`, `to_facility`, `default_inbound_order_type`, `inbound_warehouse_id`, `inbound_to_facility`, `shipping_service_key`, `default_shipping_service`), behavior toggles (`auto_reconcile_inventory`, `inventory_location_id`, `auto_send_packing_slip`, `auto_validate_delivery`), sync bookkeeping (`last_inventory_sync`, `last_shipment_sync`).
- `synapse.inventory.line` — inventory snapshot: facility/custid/item/lot/uom/class/status fields, `qty`, `weight`, `cube`, `product_id`, computed `odoo_qty`/`variance` (location-scoped, batched) and stored `matched`, `last_sync`. Ordered by item, lot_number.
- `synapse.shipment` — shipped-order records: `orderid`, `shipid`, `facility`, `custid`, `po_number`, `reference`, `order_status` + computed label, `date_shipped`, `carrier`, `scac`, `prono`, `delivery_service`, `qty_ship`, `weight_ship`, `shipping_cost`, links `picking_id`, `sale_order_id`, `line_ids`, `last_sync`. SQL unique `(connection_id, orderid, shipid)`.
- `synapse.shipment.line` — per-carton lines: `lpid`, `item`, `lot_number`, `quantity`, `uom`, `weight`, `tracking_number`, `carton_type`, `carton_sequence`.
- `synapse.queue.job` — sync queue: `connection_id`, `job_type` (inventory/shipment/order/update/resync/attachment/cancel/purchase_order/purchase_resync/purchase_cancel), `direction` (import/export), `state` (new/progress/done/failed), `payload` (JSON text), `res_model`/`res_id`, `synapse_ref`, `message`, `attempts`, `last_attempt`.
- `synapse.log` — sync log: `connection_id`, `queue_job_id`, `job_type`, `direction`, `state` (success/failed), `model`, `res_ref`, `message`; `_log()` helper.
- `synapse.dashboard` — kanban dashboard with computed counts (connections, inventory, shipments, orders, POs, queue by state, failed logs) and card navigation actions.
- Wizards (TransientModel): `synapse.order.wizard` (+ `synapse.order.wizard.line`), `synapse.purchase.order.wizard` (+ `.line`), `synapse.attachment.upload.wizard`.

Inherited models:
- `sale.order` — `synapse_connection_id`, `synapse_orderid`, `synapse_shipid`, `synapse_wave`, `synapse_status` (draft/sent/failed/shipped/cancelled), `synapse_order_status` (granular WMS selection), `synapse_to_send`, `synapse_shipped_date`, `synapse_customer_packing_list`, `synapse_shipment_ids`, computed `synapse_shipment_count`.
- `purchase.order` — inbound mirror of the SO fields; `synapse_status` ends at `received`; adds `synapse_received_date`.
- `stock.picking` — same Synapse field set (`synapse_connection_id`, `synapse_orderid`, `synapse_shipid`, `synapse_wave`, `synapse_status`, `synapse_order_status`, `synapse_to_send`, `synapse_shipped_date`, `synapse_customer_packing_list`, `synapse_shipment_ids`, computed `synapse_shipment_count`).
- `delivery.carrier` — `synapse_service_code` (max 4) and `synapse_carrier_code` (max 10).

## Business Logic & Overrides
`synapse.connection` (models/synapse_connection.py, ~2200 lines — the core):
- Module constants: `SYNAPSE_ORDER_TYPES`, `SYNAPSE_INBOUND_ORDER_TYPES`, `SYNAPSE_ORDER_STATUSES`, `SYNAPSE_ORDER_PRIORITIES`, per-field `SYNAPSE_HEADER_MAXLEN`/`SYNAPSE_DETAIL_MAXLEN`/`SYNAPSE_UPDATE_MAXLEN`, `SYNAPSE_ERROR_HINTS`, carrier auto-detection tables.
- HTTP layer: `_headers()` (Bearer / x-auth-token / SYNAPSE-SESSION cookie), `_call()` (JSON POST with 401/403 session re-login + retry), `_call_multipart()` (file uploads), `_format_error()` (API error codes + remediation hints), `_login()`/`_logout()`, `test_connection()`.
- Orders Odoo→Synapse: `build_order_payload()` (outbound header + details, blank-field drop + truncation, on-hand pre-flight `_validate_order_items()`), `build_inbound_order_payload()` (PO receipt: warehouse address as ship_to, vendor as shipper, to_facility, no on-hand check), `push_order()` / `push_inbound_order()` (create-order with idempotency guard on `synapse_orderid` and immediate `cr.commit()` of returned ids), `build_order_update_payload()` / `update_order()` (header-only shipping/routing update; strict service-code validation), `cancel_order()` (mhe/cancel-order, verified via order-info status X), `resync_order()` / `resync_inbound_order()` (cancel + recreate for content changes), `get_order_info()` (int-coerced ids), `refresh_order_status()` (maps WMS status milestones: 9→shipped + date stamp, X→cancelled, A/R→received for POs; triggers auto delivery validation), enqueue wrappers `enqueue_order`/`enqueue_inbound_order`/`enqueue_update_order`.
- Line filters: `_is_pickable_line()` (excludes display_type, is_delivery, service products), `_is_receivable_line()`; `_build_synapse_details()` enforces SKU = `default_code` only, whole positive integer quantities, default UoM.
- Shipping codes: `_order_shipping_service()` (override → carrier `synapse_service_code` → default; strict mode rejects truncated names), `_order_carrier_code()` + `_guess_carrier_code()` (delivery_type/name keyword detection).
- Inventory: `_fetch_inventory_rows()` (by-customer / by-item), `enqueue_inventory()` (one import job per row), `pull_inventory()` (inline sync), `_synapse_available_map()` (snapshot-derived availability used by SO confirmation).
- Shipments: `_fetch_shipment_rows()` (orders/shipped-orders range; API-00080 treated as empty), `enqueue_shipments()`, `pull_shipments()`.
- Reconciliation: `reconcile_inventory()` (force-correct variances via inventory_mode quants), `_reconcile_stuck_orders()` (retry failed jobs, report orphans), `_send_reconcile_order_email()` / `_send_reconcile_inventory_email()` (templates), `_run_synapse_reconciliation()`, `cron_reconcile_synapse()`, `action_reconcile_synapse()`.
- Attachments: `list_attachments()`, `download_attachment()`, `upload_attachment()` (multipart), `fetch_attachments_to_order()` (dedupe via `synapse:<file_id>` description), `upload_order_attachments()` (uploads SO + picking attachments; marks `synapse_up:<file_id>`), `_generate_packing_slip()` (renders `stock.report_deliveryslip` PDF onto the SO, idempotent, skipped for Customer Packing List orders), `_auto_send_packing_slip()` (post-send upload with failure fallback job), `action_send_pending_packing_slips()`.
- Crons on connection: `cron_push_orders()` (enqueue flagged confirmed SOs, dedupe pending jobs), `cron_retry_insufficient_inventory_orders()` / `_retry_insufficient_inventory_orders()` (45-min: refresh stock per SKU, `action_assign()` pickings, re-run failed jobs), `cron_refresh_order_statuses()`, `cron_pull_inventory()`, `cron_pull_shipments()`, `cron_reconcile_synapse()`.

`sale.order` (models/sale_order.py):
- `action_confirm()` OVERRIDE — `_synapse_route_order_lines()`: for dropship-marked lines, decides fulfilment from combined availability (Synapse snapshot + Odoo free qty excluding the Synapse mirror location `_odoo_free_excl_synapse()`): full stock → delivery route; none + vendor → dropship RFQ; partial → split the line (available qty on delivery route, shortfall copied to a dropship line); posts a chatter summary of decisions.
- Buttons/actions: `action_send_to_synapse` (opens wizard), `action_update_synapse`, `action_resync_synapse`, `action_cancel_synapse`, `action_refresh_synapse_status`, `action_fetch_synapse_attachments`, `action_upload_synapse_attachments`, `action_upload_file_to_synapse`, `action_view_synapse_shipments` (smart button).
- Reports: `cron_send_synapse_daily_report()` (5pm Pacific via `SYNAPSE_REPORT_TZ`; shipped-today / generated-today / aging sections, row builders, template `mail_template_synapse_daily_report`); `cron_send_freeport_ready_orders_report()` (11AM Pacific; pickings that became Ready in last 24h, found via mail.tracking.value + write_date; template `mail_template_freeport_ready_orders_report`).

`purchase.order` (models/purchase_order.py): inbound action buttons (`action_send_to_synapse` opens inbound wizard with confirm/already-sent guards, `action_resync_synapse`, `action_cancel_synapse`, `action_refresh_synapse_status`); `_synapse_connection()` fallback chain.

`stock.picking` / `stock.quant` (models/stock.py): picking gets the same action set as SO plus `action_upload_file_to_synapse`; `_synapse_shipment_domain()` falls back on orderid / `po_number` = picking name. `stock.quant.action_pull_synapse_inventory()` refreshes the snapshot per product.

`synapse.shipment` (models/synapse_shipment.py): `_upsert_from_synapse()` (dedupe/update, `_match_picking()` / `_match_sale_order()` linking, `_sync_lines()` per-carton rows), `_write_back_to_picking()` / `_write_back_to_order()` (status + shipped-date stamps), `_auto_validate_delivery_picking()` / `_auto_validate_order_delivery()` (set done quantities from shipment lines, validate picking, create backorders for unfulfilled items), `action_view_picking` / `action_view_sale_order` buttons, `_compute_order_status_label`.

`synapse.queue.job` (models/synapse_queue_job.py): `_enqueue()` (JSON payload serialization), `process()` (dispatch, log success/failure, chatter on failure, `synapse_status='failed'` for send jobs, status emails to group `group_synapse_notification`, commit after failure), `_dispatch()` (routes every job_type/direction to the connection methods), `_rebuild_line_values()`, `action_process`/`action_requeue`/`process_now`, `_is_retryable_stuck_job()`/`_retry_stuck_order_jobs()`, `cron_process_queue()` (advisory-locked drain, limit 500), `cron_vacuum_jobs()` (delete done jobs > 7 days).

`synapse.inventory.line`: batched `_compute_odoo_qty_and_variance`, `_import_row()` (upsert per connection/facility/item/lot/uom + optional auto-reconcile), `_reconcile_to_odoo()` (inventory_mode quant creation + `action_apply_inventory`).

`synapse.dashboard`: `_compute_counts`, `_get_dashboard()`, `action_open_dashboard`, card window actions (`get_connections`, `get_inventory`, `get_shipments`, `get_orders`, `get_purchase_orders`, `get_queue_*`, `get_failed_logs`), `action_process_queue`, `action_reconcile_synapse`.

Wizards:
- `synapse.order.wizard` — `default_get()` pre-fills lines/carrier/PO# from SO or picking; `action_send()` writes Synapse flags on target, enqueues and immediately `process_now()`s the job, raising on failure.
- `synapse.purchase.order.wizard` — inbound equivalent; may override `po.partner_ref`; enqueues + processes now.
- `synapse.attachment.upload.wizard` — `action_upload()` pushes an arbitrary file immediately.

## UI & Views
- `views/synapse_dashboard_views.xml` — kanban dashboard `view_synapse_dashboard_kanban` (Process Queue / Reconcile buttons), server action `action_synapse_dashboard`, root menu `menu_synapse_root` ("Synapse") + Dashboard menu. CSS asset `static/src/css/synapse_dashboard.css` (web.assets_backend).
- `views/synapse_connection_views.xml` — connection form (Test Connection, Pull Inventory, Pull Shipments, Process Queue, Send Packing Slips buttons), list, action + Configuration/Connections menus.
- `views/synapse_inventory_views.xml` — inventory list/search (variance columns), action + Inventory menu.
- `views/synapse_shipment_views.xml` — shipment list/form (View Picking / View Sale Order buttons), action + Shipments menu.
- `views/synapse_queue_views.xml` — queue list/form (Process / Requeue buttons), search, action + Sync Queue menu.
- `views/synapse_log_views.xml` — log list/form/search + Logs menu.
- `views/sale_order_views.xml` — `view_order_form_synapse` inherits `sale.view_order_form`: shipments smart button in `//div[@name='button_box']`, Synapse page in notebook; separate Synapse list/search views + `action_synapse_orders` + Orders menu.
- `views/purchase_order_views.xml` — `view_purchase_order_form_synapse` inherits `purchase.purchase_order_form` (header buttons Send/Resync/Refresh/Cancel + Synapse page), list/search, action + Inbound Orders menu.
- `views/stock_picking_views.xml` — `view_picking_form_synapse` inherits `stock.view_picking_form` (button_box smart button, header buttons Send/Update/Resync/Cancel/Refresh/Upload File + Synapse page), list/search, action + Deliveries menu.
- `views/delivery_carrier_views.xml` — `view_delivery_carrier_form_synapse` inherits `delivery.view_delivery_carrier_form` (Synapse codes).
- Wizard views: `wizard/synapse_order_wizard_views.xml`, `wizard/synapse_purchase_order_wizard_views.xml`, `wizard/synapse_attachment_wizard_views.xml`.

## Endpoints, Reports & Services
- No HTTP controllers (outbound REST client only, via `requests`, default timeout 60s; endpoints: login, logout, orders/create-order, orders/update-order, orders/order-info, orders/shipped-orders, mhe/cancel-order, inventory/by-customer, inventory/by-item, attachments, attachments/download, attachments/upload).
- Mail templates (`data/mail_template.xml`): `email_template_synapse_order_status`, `email_template_synapse_purchase_status` (per-order send/failure notifications), `mail_template_synapse_daily_report`, `mail_template_synapse_reconcile_order`, `mail_template_synapse_reconcile_inventory`, `mail_template_freeport_ready_orders_report`.
- Crons (`data/synapse_cron.xml`): pull inventory (daily 6:00, inactive), pull shipments (4h, inactive), push orders (daily 23:00, inactive), process queue (10 min, inactive), daily orders report (daily 00:00 UTC, active), reconcile orders & inventory (daily 23:30, active), refresh order statuses (45 min, active), retry insufficient-inventory orders (45 min, active), vacuum done jobs (daily 02:00, inactive), Freeport ready orders report (daily 18:00 UTC, active).
- Packing slip PDF rendering uses core report `stock.report_deliveryslip` (`ir.actions.report._render_qweb_pdf`).
- Tests: `tests/test_sale_order.py`, `tests/test_synapse_packing_slip.py`, `tests/test_synapse_shipment.py`.

## Security & Access
- Group `group_synapse_notification` ("Synapse Order Notifications", hidden category) — receives per-order send/failure emails.
- Record rule override: `security/sale_extension_rule_override.xml` rewrites `sale_extension.sale_extention_supplier_user_rule` domain to `['|', ('partner_id.country_id','=',False), ('partner_id.country_id.code','!=','CN')]` so vendors without a country stay accessible.
- `ir.model.access.csv`: full CRUD on ALL Synapse models (connection, inventory.line, shipment(+line), wizards, queue.job, log, dashboard) for `base.group_user`.

## Dependencies & Load Order
- Depends: `base`, `stock`, `sale`, `sale_stock`, `purchase`, `purchase_stock`, `delivery`, `stock_dropshipping`, `sale_extension` (local). External Python: `requests`, `pytz`.
- Data sequence: `security/ir.model.access.csv`, `security/security.xml`, `security/sale_extension_rule_override.xml`, `data/synapse_cron.xml`, `data/mail_template.xml`, then views (dashboard, connection, inventory, shipment, queue, log, sale_order, stock_picking, purchase_order, delivery_carrier), then the three wizard views.
