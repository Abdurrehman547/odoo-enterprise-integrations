# -*- coding: utf-8 -*-
{
    'name': "Duoplane Dropship & Order Automation Integration",
    'version': "18.0.1.0.0",
    'category': "Inventory/Delivery",
    'summary': "Automate vendor dropship purchase order ingestion, SKU mapping, and delivery tracking via Duoplane API",
    'description': """
Duoplane Dropship & Order Automation Connector
==============================================
Enterprise integration between Odoo ERP and the Duoplane dropshipping management platform:

Key Features:
-------------
* Automated Vendor PO Ingestion into confirmed Odoo Sales Orders
* Intelligent SKU Catalog Mapping & Inventory Stock Validation
* Dynamic Freight Markup & Shipping Carrier Integration
* Automated Post-Delivery Invoicing & Status Reconciliation
* Scheduled Cron Synchronization with comprehensive error handling & retry queues
    """,
    'author': "Abdur Rehman",
    'website': "https://github.com/Abdurrehman547",
    'license': 'LGPL-3',
    'depends': [
        'base',
        'sale_management',
        'stock',
        'product',
        'delivery',
        'website_sale_stock',
        'website_sale',
    ],
    'external_dependencies': {
        'python': ['openpyxl', 'requests'],
    },
    'data': [
        'security/ir.model.access.csv',
        'wizard/duoplane_sync_wizard_views.xml',
        'data/duoplane_config_data.xml',
        'data/ir_cron.xml',
        'views/duoplane_config_views.xml',
        'views/sale_order_views.xml',
        'views/duoplane_menu.xml',
        'views/delevery_carrier.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'duoplane_integ/static/src/xml/product_availability.xml',
        ],
    },
    'demo': [],
    'installable': True,
    'application': True,
    'auto_install': False,
}
