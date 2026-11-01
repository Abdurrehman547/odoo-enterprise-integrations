# -*- coding: utf-8 -*-
from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # Meta Ads Fields
    meta_access_token = fields.Char(
        string='Meta Access Token',
        config_parameter='ads_integration.meta_access_token',
        help='Access token for Meta Ads API'
    )
    meta_pixel_id = fields.Char(
        string='Meta Pixel ID',
        config_parameter='ads_integration.meta_pixel_id',
        help='Pixel ID for Meta Ads tracking'
    )
    meta_test_event_code = fields.Char(
        string='Meta Test Event Code',
        config_parameter='ads_integration.meta_test_event_code',
        help='Test event code for Meta Ads (optional)'
    )

    # Bing Ads Fields
    bing_account_id = fields.Char(
        string='Bing Account ID',
        config_parameter='ads_integration.bing_account_id',
        help='Account ID for Bing Ads'
    )
    bing_client_id = fields.Char(
        string='Bing Client ID',
        config_parameter='ads_integration.bing_client_id',
        help='Client ID for Bing Ads API'
    )
    bing_client_secret = fields.Char(
        string='Bing Client Secret',
        config_parameter='ads_integration.bing_client_secret',
        help='Client Secret for Bing Ads API'
    )
    bing_customer_id = fields.Char(
        string='Bing Customer ID',
        config_parameter='ads_integration.bing_customer_id',
        help='Customer ID for Bing Ads'
    )
    bing_developer_token = fields.Char(
        string='Bing Developer Token',
        config_parameter='ads_integration.bing_developer_token',
        help='Developer Token for Bing Ads API'
    )
    bing_refresh_token = fields.Char(
        string='Bing Refresh Token',
        config_parameter='ads_integration.bing_refresh_token',
        help='Refresh Token for Bing Ads API'
    )
    bing_tenant_id = fields.Char(
        string='Bing Tenant ID',
        config_parameter='ads_integration.bing_tenant_id',
        help='Tenant ID for Bing Ads'
    )
    # Conversion feedback-loop settings (Odoo -> Ads platforms)
    bing_qualified_goal_name = fields.Char(
        string='Bing Qualified Lead Goal',
        config_parameter='ads_integration.bing_qualified_goal_name',
        help='Exact goal name in Microsoft Advertising for the qualified-lead '
             'conversion, e.g. "Odoo – Qualified Lead (Opportunity)"'
    )
    conversion_failure_emails = fields.Char(
        string='Conversion Failure Alert Emails',
        config_parameter='ads_integration.conversion_failure_emails',
        help='Comma-separated list emailed whenever a conversion upload to '
             'Google Ads or Microsoft Ads fails (e.g. "ben@x.com, dev@y.com")'
    )

    auto_conversion_start_date = fields.Char(
        string='Auto Conversion Start Date',
        config_parameter='ads_integration.auto_conversion_start_date',
        help='Won/lost conversions are only discovered and sent automatically '
             'for leads closed/archived on or after this date (YYYY-MM-DD). Older records '
             'must go through the mass-upload wizard. Default: deploy day.'
    )

    bing_won_goal_name = fields.Char(
        string='Bing Closed Won Goal',
        config_parameter='ads_integration.bing_won_goal_name',
        help='Exact goal name in Microsoft Advertising for the Closed Won '
             'conversion, e.g. "Odoo - Lead/Closed Won"'
    )
