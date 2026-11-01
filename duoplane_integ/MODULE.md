# duoplane_integ

## Module Overview
Duoplane dropship and e-commerce vendor integration for Odoo 18. Bi-directional REST integration between Odoo and Duoplane: inbound purchase order polling and conversion into Odoo Sale Orders (assigned to Andline Distribution LLC under parent partner VRW with global cross-company duplicate detection, strict vendor SKU resolution, and auto-confirmation), automatic shipping method assignment and rate markup calculation (percentage + fixed margin with free-shipping threshold), outbound shipment and tracking export to Duoplane on delivery validation via isolated post-commit execution, automatic customer invoicing and EDI email dispatch upon shipment confirmation, batch retailer SKU importing from Excel, and website stock availability threshold formatting.

## Data Models & Schema
New models:
- `duoplane.config` — API configuration and sync engine: `name`, `active`, credentials (`api_url`, `api_key`, `api_password`), sync preferences (`auto_create_products`, `auto_create_partners`, `default_product_type`, `default_product_categ_id`, `delivery_carrier_id`), sync filters (`sync_date_from`, `sync_unfulfilled_only`), sync status (`last_sync_date`, `last_sync_status`, `last_sync_message`), and cumulative counters (`total_orders_synced`, `total_products_created`, `total_partners_created`).
- `duoplane.sync.wizard` — TransientModel for manual on-demand synchronization: `config_id`, `sync_date_from`, `sync_unfulfilled_only`.
- `product.retailer.sku.import` — TransientModel for automated Excel-based SKU import: reads bundled Excel spreadsheet to map retailer SKUs to product variants.

Inherited models:
- `sale.order` — `x_duoplane_po_id` (Duoplane PO integer ID), `x_retailer` (retailer name string), `x_duoplane_synced` (stored computed boolean), `x_duoplane_shipment_posted` (boolean flag).
- `sale.order.line` — `x_duoplane_order_item_id` (Duoplane order item integer ID required for shipment export).
- `stock.picking` — `x_duoplane_shipment_id` (returned Duoplane shipment ID), `x_duoplane_shipment_posted` (boolean status flag), `x_duoplane_shipment_error` (API failure text), `x_duoplane_invoice_ids` (M2M relation to `account.move` tracking invoices auto-generated for the picking).
- `delivery.carrier` — Duoplane-specific pricing rules: `x_duoplane_margin` (percentage markup), `x_duoplane_fixed_margin` (additional fixed fee), `x_duoplane_free_over` (boolean free shipping toggle), `x_duoplane_free_over_amount` (free shipping order total threshold).
- `product.product` — `x_duoplane_product` (boolean flag set during context import), `x_retailer_sku` (retailer SKU string).
- `product.template` — `x_retailer_sku` (indexed retailer SKU string).
- `res.partner` — `x_duoplane_customer` (boolean flag marking partners auto-created via Duoplane integration).

## Business Logic & Overrides
`duoplane.config` (models/duoplane_config.py — integration core):
- API Authentication & Verification: `_check_credentials()` enforces minimum API key length; `action_test_connection()` pings `{api_url}/purchase_orders.json` with `HTTPBasicAuth` (timeout 30s) and returns a success notification or raises `UserError`.
- Order Polling: `_fetch_duoplane_orders()` queries `{api_url}/purchase_orders.json` filtered by `search[updated_at_min]` and `search[fulfilled]='0'` (page size 250, timeout 60s); `cron_sync_orders()` iterates active configurations.
- Synchronization Engine: `sync_orders()` processes fetched orders wrapped inside DB savepoints (`self.env.cr.savepoint()`), tracking granular metrics (orders, products, partners, errors) and recording outcomes to `last_sync_status` and `last_sync_message`.
- Sale Order Generation: `_create_sale_order_from_duoplane()` forces order creation under company "Andline Distribution LLC" (`res.company`), performs global multi-company duplicate prevention by querying both `x_duoplane_po_id` and `client_order_ref` using `.sudo()`, maps customer address under company "VRW", populates order lines with `x_duoplane_order_item_id`, applies configured `delivery_carrier_id`, sets commitment date and notes, and auto-confirms the sale order via `action_confirm()`.
- Partner Resolution: `_get_or_create_partner()` looks up active parent company named "VRW", matches existing delivery child contacts by capitalized customer name, or creates child delivery contacts (`type='delivery'`, `x_duoplane_customer=True`, default phone `+1 631 540 1261`, country and state mapped).
- Strict Product Matching: `_get_or_create_product()` implements strict vendor SKU resolution (`vendor_sku` compared strictly against `product.product.default_code`), intentionally refusing automatic product creation or retailer SKU fallback to protect inventory integrity, while storing `retailer_sku` on matched variants.
- Vendor Info Helpers: `_get_or_create_vendor()`, `_create_product_vendor()`, and `_ensure_product_vendor()` manage supplier records (`product.supplierinfo`) based on retailer metadata.

`sale.order` & `sale.order.line` (models/sale_order.py):
- `action_confirm()` OVERRIDE — triggers `_auto_apply_duoplane_shipping()` for Duoplane-synced orders.
- Shipping Automation: `_resolve_duoplane_carrier()` selects carrier from order, active config `delivery_carrier_id`, or fallback "UPS US". `_auto_apply_duoplane_shipping()` prevents duplicate delivery lines, calculates non-delivery line weight, invokes the delivery wizard (`choose.delivery.carrier`), calls `update_price()`, and executes `button_confirm()` / `button_update()` (or fallback `set_delivery_line`) to inject rated shipping onto the SO.

`stock.picking` (models/stock_picking.py):
- `_action_done()` OVERRIDE — for outgoing pickings (`picking_type_id.code == 'outgoing'`), registers a post-commit hook (`self.env.cr.postcommit.add`) that opens a clean isolated database cursor (`self.env.registry.cursor()`) to execute `_process_duoplane_and_invoice_postcommit()` safely outside the core stock transaction.
- Outbound Shipment Posting: `_post_shipment_to_duoplane()` checks tracking number presence, resolves parent Duoplane SO and PO ID, fetches configuration, maps carrier name (`UPS`, `FedEx`, `USPS`, `DHL`, `OnTrac`), compiles done move quantities grouped by `x_duoplane_order_item_id`, formats tracking references, retrieves shipping cost via `_get_shipping_cost()`, and POSTs JSON to `{api_url}/purchase_orders/{duoplane_po_id}/shipments.json`. On HTTP 200/201, records `x_duoplane_shipment_id`, recalculates SO carrier rating to re-apply margin markup, and logs chatter notification.
- Order Data Re-Sync: `_sync_duoplane_order_data()` acts as a self-healing fallback if lines lack `x_duoplane_order_item_id` or SO lacks `x_duoplane_po_id`, fetching order details from Duoplane and mapping line items by vendor/retailer SKU.
- Automated Invoicing: `_create_customer_invoice_after_duoplane_post()` triggers only after verified shipment post. Uses `sale_order._create_invoices()` to invoice strictly delivered quantities, stores invoice links in `x_duoplane_invoice_ids` to prevent duplicate billing per delivery, posts the invoice (`action_post()`), and invokes `_send_invoice_email()`.
- Automated Email: `_send_invoice_email()` finds mail template `'Invoice: Sending'` (or fallback `'account.email_template_edi_invoice'`), renders PDF attachment via `invoice._generate_pdf_and_send_invoice(template)`, and dispatches email immediately (`force_send=True`).
- Manual Retry: `action_retry_duoplane_shipment()` allows manual re-posting of failed shipments and subsequent invoice generation directly from picking form.

`delivery.carrier` (models/delevery_carrier_inherit.py):
- `rate_shipment()` OVERRIDE — computes delivery pricing. For Duoplane orders, applies Duoplane margin rules (`x_duoplane_margin` percentage markup + `x_duoplane_fixed_margin` additional cost) and checks free shipping qualification (`x_duoplane_free_over` and `x_duoplane_free_over_amount`). Returns both `price` and `carrier_price` in the dictionary to ensure compatibility with standard delivery wizards.

`product.retailer.sku.import` (models/automatid_action.py):
- `cron_import_retailer_sku()` loads `static/xlsx/Texas_Distribution_SKUs.xlsx` via `openpyxl`, iterates rows with savepoints, matches product variants by `barcode` or `default_code`, and updates `x_retailer_sku`.

`res.partner` & `product.product` / `product.template`:
- Inherit `create()` to automatically tag records (`x_duoplane_customer=True`, `x_duoplane_product=True`) when executed under `duoplane_import` context.

`duoplane.sync.wizard` (wizard/duoplane_sync_wizard.py):
- `action_sync()` temporarily overrides configuration dates and unfulfilled filters, invokes `config_id.sync_orders()`, restores previous settings, and returns an interactive client notification.

## UI & Views
- `views/duoplane_config_views.xml` — configuration form (`view_duoplane_config_form`) with Test Connection and Sync Now header buttons, manual sync stat button, API Credentials group, Sync Settings group, and Sync Status metrics; window action `action_duoplane_config`.
- `views/sale_order_views.xml` — `view_order_form_duoplane` inherits `sale.view_order_form` adding Duoplane PO ID and Retailer name under `client_order_ref`; `view_order_tree_duoplane` inherits `sale.view_quotation_tree` with optional columns; window action `action_duoplane_orders` filtering for Duoplane-synced orders; cron definition for retailer SKU import.
- `views/duoplane_menu.xml` — root menu `menu_duoplane_root` ("Duoplane", sequence 50) with submenus `menu_duoplane_config` ("Configuration" > "Settings") and `menu_duoplane_orders` ("Orders").
- `views/delevery_carrier.xml` — `view_delivery_carrier_form_duoplane_page` inherits `delivery.view_delivery_carrier_form`, adding notebook tab "Duoplane Pricing" for non-fixed/rule carrier methods.
- `wizard/duoplane_sync_wizard_views.xml` — modal dialog form (`view_duoplane_sync_wizard_form`) and window action `action_duoplane_sync_wizard` for manual order synchronization.
- Frontend web assets:
  - `views/product_availability.xml` & `static/src/xml/product_availability.xml` — QWeb extension of `website_sale_stock.product_availability` capping threshold stock messages to display "1000 [UoM] left in stock" when available stock exceeds 1000.
  - `static/src/js/product_availability.js` — Owl component patch script for stock formatting.

## Endpoints, Reports & Services
- External REST API Client (via `requests`, `HTTPBasicAuth`, JSON):
  - `GET /purchase_orders.json` — fetch purchase orders filtered by date and unfulfilled status; test connection.
  - `GET /purchase_orders/{id}.json` — retrieve individual order detail and line item IDs for SKU mapping.
  - `POST /purchase_orders/{id}/shipments.json` — export shipment fulfillments with tracking numbers, item quantities, and carrier details.
- Mail & EDI:
  - Invoices automatically rendered to PDF and emailed via `'Invoice: Sending'` or `account.email_template_edi_invoice`.
- Crons:
  - `ir_cron_duoplane_sync` (`data/ir_cron.xml`): every 15 minutes, calls `model.cron_sync_orders()` on `duoplane.config` (inactive by default).
  - `ir_cron_import_retailer_sku` (`views/sale_order_views.xml`): daily, calls `model.cron_import_retailer_sku()` on `product.retailer.sku.import` (inactive by default).
- Static Data & Assets:
  - Bundled SKU mapping spreadsheet: `static/xlsx/Texas_Distribution_SKUs.xlsx`.

## Security & Access
- Record Access Rights (`security/ir.model.access.csv`):
  - `duoplane.config`: read-only for `sales_team.group_sale_salesman`, full CRUD for `sales_team.group_sale_manager`.
  - `duoplane.sync.wizard`: full CRUD for `sales_team.group_sale_salesman`.
  - `product.retailer.sku.import`: full CRUD for `base.group_system`.
- Multi-company Security:
  - Cross-company order creation and global duplicate lookups explicitly invoke `.sudo()` to cross company isolation between "Andline Distribution LLC" and other database entities.

## Dependencies & Load Order
- Depends: `base`, `sale_management`, `stock`, `product`, `delivery`, `website_sale_stock`, `website_sale`. External Python: `openpyxl`, `requests`.
- Assets (`web.assets_frontend`): `duoplane_integ/static/src/xml/product_availability.xml`.
- Data sequence in manifest:
  1. `security/ir.model.access.csv`
  2. `wizard/duoplane_sync_wizard_views.xml`
  3. `data/duoplane_config_data.xml`
  4. `data/ir_cron.xml`
  5. `views/duoplane_config_views.xml`
  6. `views/sale_order_views.xml`
  7. `views/duoplane_menu.xml`
  8. `views/delevery_carrier.xml`
