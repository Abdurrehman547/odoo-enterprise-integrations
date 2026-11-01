# 🚀 Enterprise Odoo Integrations Suite (v17 - v19)

<div align="center">

[![Odoo](https://img.shields.io/badge/Odoo-v17%20%7C%20v18%20%7C%20v19-714B67?style=for-the-badge&logo=odoo&logoColor=white)](#)
[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](#)
[![License: LGPL v3](https://img.shields.io/badge/License-LGPL_v3-blue.svg?style=for-the-badge)](https://www.gnu.org/licenses/lgpl-3.0)
[![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg?style=for-the-badge)](#)
[![Author](https://img.shields.io/badge/Author-Abdur%20Rehman-orange?style=for-the-badge&logo=github)](https://github.com/Abdurrehman547)

<p align="center">
  <b>Production-grade, enterprise-scale Odoo ERP connectors developed for US & international high-volume operations.</b><br>
  Featuring Cloud Storage Sync, Warehouse Management Systems (WMS), Multi-Vendor Dropship Automation, and Multi-Channel Ad Attribution.
</p>

</div>

---

## 📦 Included Enterprise Modules

| Module Directory | Technical Name | Category | Supported Odoo | Key Highlights |
| :--- | :--- | :--- | :---: | :--- |
| **[`google_drive_integration`](./google_drive_integration)** | `google_drive_integration` | Cloud Storage | **v18, v19** | OAuth 2.0 Web flow, automatic contact/lead folder creation, direct upload wizard, live preview links. |
| **[`dropbox_integration`](./dropbox_integration)** | `dropbox_integration` | Cloud Storage | **v18, v19** | Secure OAuth 2.0 Dropbox cloud storage, attachment synchronization, automated token refresh. |
| **[`bha_synapse`](./bha_synapse)** | `bha_synapse` | WMS & Logistics | **v18** | High-throughput Synapse Anywhere REST pipeline, carton-level tracking, inventory snapshot sync & variance reconciliation. |
| **[`duoplane_integ`](./duoplane_integ)** | `duoplane_integ` | Dropshipping & Sales | **v17, v18** | Duoplane dropship order ingestion, automatic SKU catalog mapping, shipping markup & vendor PO sync. |
| **[`crm_lead`](./crm_lead)** | `crm_lead` | Marketing & CRM | **v18** | REST/Webhook lead ingestion, UTM/click-id tracking, Meta Conversions API (CAPI) & Microsoft Bing Ads SOAP queues. |

---

## 🏗️ Architecture & High-Level Capabilities

### 1. ☁️ Cloud Document Management (`google_drive_integration` & `dropbox_integration`)
* **Standard-Based Authentication**: Implements standard OAuth 2.0 Web Application Authorization Code grant with CSRF state token verification.
* **Non-Blocking Token Rotation**: Refreshes expired access tokens transparently without interrupting user operations.
* **Native Record Linkage**: Automatically structures directories inside Google Drive / Dropbox mapped to Odoo Partners (`res.partner`) and CRM Opportunities (`crm.lead`).

### 2. 🏭 Synapse Anywhere WMS Connector (`bha_synapse`)
* **Bi-Directional Logistics Synchronization**: Connects Odoo picking, delivery orders, and purchase orders to Synapse WMS.
* **Asynchronous Queue Jobs**: Built-in retry mechanism with exponent backoff for API timeouts and network resilience.
* **Document Transfer**: Bi-directional Base64 document encoding/decoding supporting commercial invoices, packing lists, and bill of lading attachments.
* **Visual Workflows**: Full `.drawio` architecture diagrams included in [`bha_synapse/doc/`](./bha_synapse/doc/).

### 3. 🚚 Duoplane Multi-Vendor Dropship Automation (`duoplane_integ`)
* **Automated Dropship Flow**: Automatically polls Duoplane endpoints to ingest vendor purchase orders and confirm corresponding Odoo sales orders.
* **Catalog Mapping**: Matches merchant and vendor SKU variations dynamically with robust fallback rules.
* **Post-Delivery Invoicing**: Automates invoicing upon carrier confirmation and delivery status update.

### 4. 📈 CRM Attribution & Offline Conversions (`crm_lead`)
* **Lead Attribution**: Captures URL query parameters (`utm_source`, `utm_medium`, `utm_campaign`, `gclid`, `msclkid`, `fbclid`) across public webhooks.
* **Server-Side Tracking**:
  * Pushes offline qualified/won sales events to **Meta Graph API (CAPI)**.
  * Sends SOAP batch conversion calls to **Microsoft Bing Campaign Management API v13**.
* **Governance**: Prevents unauthorized salesperson re-assignments on confirmed enterprise sales quotations.

---

## ⚙️ Installation & Setup

1. **Clone the repository** into your Odoo custom addons directory:
   ```bash
   cd /path/to/odoo/custom_addons
   git clone https://github.com/Abdurrehman547/odoo-enterprise-integrations.git
   ```

2. **Add to Odoo Configuration**:
   Ensure `odoo-enterprise-integrations` is included in your `odoo.conf` file under `addons_path`:
   ```ini
   addons_path = /path/to/odoo/addons,/path/to/odoo/custom_addons/odoo-enterprise-integrations
   ```

3. **Install Dependencies**:
   ```bash
   pip install google-api-python-client dropbox openpyxl websocket-client requests
   ```

4. **Update App List & Install**:
   * Activate Developer Mode in your Odoo instance.
   * Navigate to **Apps** -> **Update Apps List**.
   * Search for any of the modules and click **Activate**.

---

## 🔒 Security & Best Practices
- **No Hardcoded Secrets**: All API keys, secrets, and credentials utilize `ir.config_parameter` with masked password fields in the settings UI.
- **Least-Privilege Access**: Comprehensive `ir.model.access.csv` rules and record security groups configured across all models.
- **Defensive Error Handling**: All network requests wrap around structured timeout guards, logging exceptions safely to standard Odoo server logs.

---

## 👨‍💻 Author & Professional Consulting

Developed and maintained by **Abdur Rehman**  
*Senior Odoo & Python Backend Specialist (3.5+ Years Experience)*

* 💼 **LinkedIn**: [linkedin.com/in/mian-abdurrehman-767a41216](https://www.linkedin.com/in/mian-abdurrehman-767a41216)
* 📧 **Email**: [marehman5478@gmail.com](mailto:marehman5478@gmail.com)
* 🌐 **GitHub**: [@Abdurrehman547](https://github.com/Abdurrehman547)

*Available for remote enterprise Odoo contracts, custom API connector architecture, and version migrations (v15 - v19).*

---

## 📄 License
This repository is licensed under the [GNU Lesser General Public License v3.0 (LGPL-3)](https://www.gnu.org/licenses/lgpl-3.0).
