# Shopify & Duoplane Multi-Retailer Integration Guide

---

## 1. Executive Summary & Current Architecture

### 1.1 What is Currently Working
* **Your Role:** **Vendor / Supplier** (Andline Distribution LLC / Texas Central Distribution).
* **Current Retailer Partner:** **VRW** (VapeRanger Wholesale).
* **Current Storefront:** VRW runs on **BigCommerce**.
* **Current Data Flow:**
  1. A customer places an order on VRW's BigCommerce store.
  2. Duoplane imports the order and creates a **Purchase Order (PO)** for your vendor account.
  3. Your Odoo 18 module (`duoplane_integ`) polls `GET /purchase_orders.json`.
  4. Odoo matches line items by **strict vendor SKU** against `product.product.default_code`.
  5. A Sales Order is auto-created under **Andline Distribution LLC**, billed to customer **VRW**, and auto-confirmed.
  6. Outbound shipments post tracking back to Duoplane (`POST /purchase_orders/{id}/shipments.json`), which updates BigCommerce and auto-sends an invoice email to VRW.

---

## 2. Onboarding the New Shopify Store Owner (Step-by-Step)

The new store owner is an independent **Retailer** selling your products on **Shopify**. They must connect their store to Duoplane and link your vendor account.

```
┌─────────────────────────────────┐       ┌─────────────────────────────────┐
│     New Shopify Store Owner     │       │     Andline Distribution LLC    │
│            (RETAILER)           │       │             (VENDOR)            │
└────────────────┬────────────────┘       └────────────────┬────────────────┘
                 │                                         │
        1. Signs up for Retailer                           │
           Account in Duoplane                             │
                 │                                         │
        2. Installs Duoplane App                           │
           from Shopify App Store                          │
                 │                                         │
        3. Enforces Unique SKUs                            │
                 │                                         │
        4. Invites Vendor ─────────────────────────────────► Accepts / Links
           (adeel@andlinellc.com)                            Vendor Profile
                 │                                         │
        5. Maps Products &                                 │
           Sets "Vendor SKU" = Odoo SKU                    │
                 │                                         │
                 │                                         │
   Customer buys on Shopify                                │
                 │                                         │
        Shopify pushes Order                               │
                 ▼                                         │
        Duoplane generates PO ─────────────────────────────► Odoo fetches PO &
                                                             Ships Order
```

---

### Step 1: The Shopify Store Owner Sets Up Duoplane
1. **Creates a Duoplane Retailer Account:**
   * The Shopify store owner signs up for a Duoplane Retailer (Merchant) plan at [duoplane.com](https://www.duoplane.com).
2. **Installs the Shopify Connector App:**
   * They visit the [Duoplane Shopify App Store page](https://apps.shopify.com/duoplane).
   * Click **Install** and approve read/write permissions for Orders, Products, and Fulfillments.
3. **Configures Unique Product SKUs (Crucial):**
   * In Duoplane, go to **Settings > Products & Inventory**.
   * Set **"Product SKUs are unique"** to **Yes**. This guarantees that product records link cleanly without creating duplicate listings.

---

### Step 2: The Retailer Connects Your Vendor Account
1. In the Duoplane Retailer portal, the store owner navigates to **Vendors > Add a Vendor**.
2. They enter your company name: **Andline Distribution LLC**.
3. In the vendor contact details, they enter your registered vendor email address: `adeel@andlinellc.com`.
4. Duoplane sends an invitation linking your existing vendor profile to their retailer account. 
5. You accept the relationship. You now have two linked retail relationships in Duoplane:
   - Relationship 1: **VapeRanger Wholesale (VRW)**
   - Relationship 2: **[New Shopify Retailer]**

---

### Step 3: Catalog & SKU Mapping (Zero Duplication)
Since the physical products are identical to what you already stock:
1. The retailer imports their Shopify products into Duoplane.
2. In Duoplane, the retailer assigns those products to your vendor record (**Andline Distribution LLC**).
3. **Strict Vendor SKU Requirement:**
   * For each product variant, the retailer must populate the **"Vendor SKU"** field with your exact Odoo internal reference (`default_code`).
   * *Why?* Your Odoo module strictly rejects lines where `vendor_sku` does not match an existing product code:
     ```python
     # Logic from duoplane_config.py:
     product = self.env['product.product'].search([('default_code', '=', vendor_sku)], limit=1)
     ```
   * As long as the retailer sets the Vendor SKU correctly, **no new products need to be created in Odoo**. Odoo will automatically match your existing stock.

---

### Step 4: Live Order & Fulfillment Execution
1. An end-customer places an order on the new Shopify storefront.
2. Duoplane imports the Shopify order and creates a new Purchase Order (PO).
3. Duoplane assigns this PO to **Andline Distribution LLC** and flags it with the new retailer's metadata.
4. Your Odoo cron job (`_fetch_duoplane_orders()`) detects the new unfulfilled PO via the Duoplane API.
5. Odoo confirms the Sale Order, generates the delivery picking, rates the shipment, and prepares the warehouse to pick and pack.
6. Once your warehouse validates the delivery in Odoo:
   - Odoo's post-commit hook posts the tracking number and carrier to Duoplane (`POST /purchase_orders/{id}/shipments.json`).
   - Duoplane receives the tracking info, recognizes that the order originated from the **Shopify store**, updates Shopify fulfillment, and triggers the shipment email to the customer.

---

## 3. How Duoplane Differentiates Both Retailers

You might wonder: *Will Duoplane mix up orders from VRW (BigCommerce) and the new Shopify retailer?*

**No. Duoplane isolates them completely at the data layer:**

| Attribute | Retailer 1 (Current) | Retailer 2 (New) |
| :--- | :--- | :--- |
| **Retailer Name** | `VapeRanger Wholesale` | `[New Retailer Name]` |
| **Retail Relationship ID** | `292373` | Unique ID generated by Duoplane |
| **Sales Channel** | BigCommerce | Shopify |
| **PO Metadata in API** | `order['retailer']['name'] = "VapeRanger Wholesale"` | `order['retailer']['name'] = "[New Retailer Name]"` |
| **Tracking Routing** | Returns tracking to BigCommerce | Returns tracking to Shopify |
| **Invoicing & Settlement** | Invoiced to VRW | Invoiced to New Retailer |

Each Purchase Order JSON payload delivered to your API contains the retailer's identity:
```json
{
  "id": 123456,
  "public_reference": "PO-1002",
  "retailer": {
    "id": 9876,
    "name": "New Shopify Retailer"
  },
  "order_items": [...]
}
```

---

## 4. What Changes are Required in Your Existing Odoo System?

Your current `duoplane_integ` module handles order creation, tracking export, and pricing. However, to cleanly support a second retailer, the following adjustments are required:

### 1. Fix Hardcoded Customer / Invoicing Partner ("VRW") — CRITICAL
* **Current Issue:** In `models/duoplane_config.py` (lines 369–376), the code hardcodes the customer lookup:
  ```python
  vrw_company = self.env['res.partner'].search([
      ('name', '=', 'VRW'),
      ('is_company', '=', True),
      ('active', '=', True)
  ], limit=1)
  ```
  If orders arrive from the new Shopify retailer, Odoo will **still assign the sale order and customer invoice to VRW**.
* **Recommended Change:**
  1. Add a `parent_partner_id` field on the `duoplane.config` model:
     ```python
     parent_partner_id = fields.Many2one(
         'res.partner',
         string='Parent Customer / Retailer',
         required=True,
         help='The parent customer (e.g. VRW or New Retailer) to whom orders and invoices are billed.'
     )
     ```
  2. Or dynamically look up / create the retailer by name:
     ```python
     retailer_name = duoplane_order.get('retailer', {}).get('name')
     parent_partner = self.env['res.partner'].search([
         ('name', 'ilike', retailer_name),
         ('is_company', '=', True)
     ], limit=1)
     ```
  *This ensures VRW is billed for VRW orders, and the new Shopify retailer is billed for their own orders.*

---

### 2. Duoplane API Configuration in Odoo
* **How Duoplane delivers multi-retailer POs:**
  * **Option A (Unified Vendor API):** Duoplane often serves all purchase orders across all your retail relationships through your single vendor API account. If this is the case, your existing Odoo configuration will fetch orders from both retailers automatically.
  * **Option B (Separate API Keys):** If Duoplane issues separate API credentials per retailer relationship, your Odoo module is already built to handle it!
    - Simply go to **Duoplane > Configuration > Settings**.
    - Click **New / Create**.
    - Name it (e.g., `Duoplane - Shopify Retailer`).
    - Enter the new API Key and Password.
    - Set the designated Parent Customer.
    - Your scheduled cron job (`cron_sync_orders()`) will loop through both configurations every 15 minutes.

---

### 3. Product Catalog
* **No changes needed.** Since the products are the same, your existing `product.product` records with their `default_code` values will match automatically without creating duplicate products in your inventory.

---

## 5. Summary Checklist

- [ ] **Shopify Store Owner:**
  - [ ] Signs up for a Duoplane Retailer Account.
  - [ ] Installs the Duoplane app from the Shopify App Store.
  - [ ] Enables `Product SKUs are unique: Yes` in Duoplane settings.
  - [ ] Adds `Andline Distribution LLC` (`adeel@andlinellc.com`) as a Vendor.
  - [ ] Enters your exact Odoo SKUs in the `Vendor SKU` field for each product variant.

- [ ] **Your Team (Andline Distribution LLC / Odoo):**
  - [ ] Accepts the vendor invite in your Duoplane vendor portal.
  - [ ] Creates a Contact record in Odoo for the new Retailer (for invoicing).
  - [ ] Updates the hardcoded `VRW` partner lookup in `duoplane_config.py` to allow dynamic or config-based parent customer assignment.
  - [ ] (If separate API keys are provided) Adds the second configuration in **Duoplane > Configuration**.
