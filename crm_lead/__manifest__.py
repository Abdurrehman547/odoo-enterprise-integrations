# -*- coding: utf-8 -*-
{
    'name': "CRM Lead Automation & Offline Ad Conversions",
    'version': "18.0.1.0.0",
    'category': "Customer Relationship Management",
    'summary': "Multi-channel lead ingestion (REST/Webhooks) with Meta Conversions API & Microsoft Bing Ads offline conversion sync",
    'description': """
CRM Lead Automation & Multi-Platform Ad Attribution
===================================================
Production-tested CRM optimization engine providing:

Key Features:
-------------
* Secure public REST endpoint (/create/crm/lead) for web forms with UTM and Click ID parsing (gclid, msclkid, fbclid)
* Meta Ads Conversions API (CAPI) background worker queue with automatic retries
* Microsoft Bing Ads SOAP offline conversion pipeline with OAuth2 token rotation
* Salesperson edit restriction governance on confirmed quotation orders
* Automated failure alerting via mail templates and chatter notifications
    """,
    'author': "Abdur Rehman",
    'website': "https://github.com/Abdurrehman547",
    'license': 'LGPL-3',
    'depends': [
        'base',
        'crm',
        'website',
        'sale',
    ],
    'external_dependencies': {
        'python': ['websocket-client', 'requests'],
    },
    'data': [
        'security/ir.model.access.csv',
        'views/views.xml',
        'views/adds_setting.xml',
        'security/security.xml',
        'data/cron_data.xml',
        'data/crm_lead_data.xml',
    ],
    'demo': [],
    'installable': True,
    'application': True,
    'auto_install': False,
}
