# -*- coding: utf-8 -*-
{
    'name': "Dropbox Integration",
    'version': "19.0.1.0.0",
    'category': "Document Management",
    'summary': "Integrate Dropbox cloud storage with Odoo records",
    'description': """This module connects Odoo to Dropbox via OAuth 2.0, enabling automated
folder creation and shareable link management directly on contacts and CRM leads.""",
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
        'views/dropbox_wizard.xml',
        'views/dropbox_upload_wizard_views.xml',
        'views/ir_attachment_views.xml',
        'wizard/authentication_wizard_views.xml',
    ],
    'external_dependencies': {
        'python': ['dropbox', 'requests'],
    },
    'installable': True,
    'auto_install': False,
    'application': True,
    'uninstall_hook': 'uninstall_hook',
}
