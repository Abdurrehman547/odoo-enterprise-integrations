# -*- coding: utf-8 -*-
{
    'name': "Google Drive Integration",
    'version': "19.0.1.0.0",
    'category': "Document Management",
    'summary': "Integrate Google Drive folders with Odoo records",
    'description': """This module connects Odoo to Google Drive via OAuth 2.0 Web Application
flow, enabling automated folder creation and shareable link management on contacts
and CRM leads. Replaces legacy Dropbox integration.""",
    'author': 'Index World',
    'company': 'Index World',
    'website': "https://www.indexworld.net",
    'license': 'AGPL-3',
    'depends': ['base', 'base_setup', 'crm'],
    'data': [
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',
        'views/res_partner.xml',
        'views/crm_lead.xml',
        'views/gdrive_wizard.xml',
        'views/gdrive_upload_wizard_views.xml',
        'views/ir_attachment_views.xml',
    ],
    'external_dependencies': {
        'python': ['google-api-python-client', 'requests'],
    },
    'installable': True,
    'auto_install': False,
    'application': True,
    'uninstall_hook': 'uninstall_hook',
}
