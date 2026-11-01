# -*- coding: utf-8 -*-
{
    'name': 'Synapse Anywhere WMS REST API Integration',
    'version': '18.0.1.0.0',
    'category': 'Inventory/Tools',
    'summary': 'Bi-directional enterprise integration between Odoo ERP and Synapse WMS via REST API',
    'description': """
Synapse Anywhere WMS Enterprise Integration
===========================================
High-throughput REST API integration connecting Odoo ERP with Synapse WMS (Synapse Anywhere):

Key Capabilities:
-----------------
* Multi-Method Authentication: Session token, Bearer, and x-auth-token management
* Outbound Order Dispatch: Automated synchronization of Sale Orders and Picking operations
* Inbound Purchase Receipts: Purchase Order tracking, carton-level updates, and validation
* Real-Time Inventory Reconciliation: Automated snapshot syncing and stock variance alerts
* Document & Attachment Pipeline: Base64 encoding/decoding and bi-directional file transfer
* Enterprise Queue Job Architecture: Resilient retry policies, exception catching, and admin dashboard
    """,
    'author': 'Abdur Rehman',
    'website': 'https://github.com/Abdurrehman547',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'stock',
        'sale',
        'sale_stock',
        'purchase',
        'purchase_stock',
        'delivery',
        'stock_dropshipping',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/security.xml',
        'data/synapse_cron.xml',
        'data/mail_template.xml',
        'views/synapse_dashboard_views.xml',
        'views/synapse_connection_views.xml',
        'views/synapse_inventory_views.xml',
        'views/synapse_shipment_views.xml',
        'views/synapse_queue_views.xml',
        'views/synapse_log_views.xml',
        'views/sale_order_views.xml',
        'views/stock_picking_views.xml',
        'views/purchase_order_views.xml',
        'views/delivery_carrier_views.xml',
        'wizard/synapse_order_wizard_views.xml',
        'wizard/synapse_purchase_order_wizard_views.xml',
        'wizard/synapse_attachment_wizard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'bha_synapse/static/src/css/synapse_dashboard.css',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
}
