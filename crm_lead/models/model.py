from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)


from .microsoft_ads_conversion_queue import _clean_msclkid

class CrmLead(models.Model):
    _inherit = "crm.lead"

    ls_gclid = fields.Char(string="Google Click ID")
    ls_utm_source = fields.Char(string="UTM Source")
    ls_utm_medium = fields.Char(string="UTM Medium")
    ls_utm_campaign = fields.Char(string="UTM Campaign")
    ls_utm_content = fields.Char(string="UTM Content")

    ls_msclkid = fields.Char(string="Microsoft Click ID")
    ls_fbclid = fields.Char(string="Facebook Click ID")

    lead_platform = fields.Selection([
        ('callrail', 'CallRail'),
        ('website', 'Website Form'),
        ('email', 'Email Gateway'),
        ('unclassified', 'Unclassified')
    ], string="Platform", compute="_compute_lead_platform", store=True)

    @api.depends('callrail_call_ids', 'source_id')
    def _compute_lead_platform(self):
        for lead in self:
            source_name = (lead.source_id.name or '').strip().lower()
            if 'callrail_call_ids' in lead._fields and lead.callrail_call_ids:
                lead.lead_platform = 'callrail'
            elif source_name in ['email', 'direct email', 'foreign lead']:
                lead.lead_platform = 'email'
            elif 'web' in source_name:
                lead.lead_platform = 'website'
            else:
                lead.lead_platform = 'unclassified'

    def write(self, vals):
        res = super(CrmLead, self).write(vals)
        # Suppress automatic queue triggers during the backfill script
        if self.env.context.get('is_backfill'):
            return res

        if 'ls_msclkid' in vals or 'ls_gclid' in vals:
            for lead in self:
                if lead.type == 'opportunity':
                    if 'ls_msclkid' in vals and 'microsoft.ads.conversion.queue' in self.env:
                        ms_queue = self.env["microsoft.ads.conversion.queue"].sudo()
                        goal_name = (self.env["ir.config_parameter"].sudo().get_param("ads_integration.bing_qualified_goal_name") or "").strip() or "Odoo - Qualified Lead"
                        clean_msclkid = _clean_msclkid(lead.ls_msclkid)
                        if clean_msclkid and not lead.ms_ads_lead_uploaded:
                            existing = ms_queue.search([("msclkid", "=", clean_msclkid), ("conversion_name", "=", goal_name), ("state", "in", ("pending", "sent"))], limit=1)
                            if not existing:
                                ms_queue.create({
                                    "lead_id": lead.id, 
                                    "msclkid": clean_msclkid, 
                                    "conversion_name": goal_name, 
                                    "conversion_time": lead.date_conversion or lead.create_date, 
                                    "conversion_value": lead.expected_revenue or 0.0
                                })
                    if 'ls_gclid' in vals and 'google.ads.conversion.queue' in self.env:
                        self.env['google.ads.conversion.queue'].enqueue(
                            lead, 
                            'qualified', 
                            conversion_time=lead.date_conversion or lead.create_date
                        )
        return res

    # Microsoft Ads flags
    ms_ads_lead_uploaded = fields.Boolean(default=False)
    ms_ads_won_uploaded = fields.Boolean(default=False)
    ms_ads_last_error = fields.Text()
    ms_ads_last_upload_at = fields.Datetime()

    # Meta Ads flags
    meta_ads_won_uploaded = fields.Boolean(default=False)
    meta_ads_last_error = fields.Text()
    meta_ads_last_upload_at = fields.Datetime()

    def action_push_to_ms_ads_queue(self):
        """Manually pushes the lead to the Microsoft Ads Queue. Pushes Qualified, and if Won, also pushes Won."""
        ms_queue = self.env['microsoft.ads.conversion.queue']
        ICP = self.env['ir.config_parameter'].sudo()
        ms_qualified_goal = (ICP.get_param('ads_integration.bing_qualified_goal_name') or '').strip() or "Odoo - Qualified Lead"
        ms_won_goal = (ICP.get_param('ads_integration.bing_won_goal_name') or '').strip() or "Odoo - Lead/Closed Won"
            
        for lead in self:
            msclkid = lead.ls_msclkid
            if not msclkid:
                lead.message_post(body="Cannot push to MS Ads Queue: Missing ls_msclkid")
                continue
                
            # 1. ALWAYS push Qualified Goal (because every Won lead was first a Qualified lead)
            if not lead.ms_ads_lead_uploaded:
                if not ms_queue.search([('msclkid', '=', msclkid), ('conversion_name', '=', ms_qualified_goal)], limit=1):
                    ms_queue.create({
                        'lead_id': lead.id,
                        'msclkid': msclkid,
                        'conversion_name': ms_qualified_goal,
                        'conversion_time': lead.create_date,
                        'conversion_value': 0.0,
                    })
                    lead.message_post(body=f"Manually queued for MS Ads ({ms_qualified_goal}).")
            else:
                lead.message_post(body=f"Lead already permanently marked as MS Ads Uploaded for {ms_qualified_goal}.")
                
            # 2. IF WON, ALSO push Won Goal
            if lead.stage_id.is_won:
                if not lead.ms_ads_won_uploaded:
                    if not ms_queue.search([('msclkid', '=', msclkid), ('conversion_name', '=', ms_won_goal)], limit=1):
                        val = lead.expected_revenue or 0.0
                        if hasattr(lead, '_google_conversion_value'):
                            val = lead._google_conversion_value()
                        ms_queue.create({
                            'lead_id': lead.id,
                            'msclkid': msclkid,
                            'conversion_name': ms_won_goal,
                            'conversion_time': lead.date_closed or lead.create_date,
                            'conversion_value': val,
                        })
                        lead.message_post(body=f"Manually queued for MS Ads ({ms_won_goal}).")

    @api.model_create_multi
    def create(self, vals_list):
        """Auto-tag and archive test submissions coming from the agency's own
        domain, so they never pollute the real lead list or the ad platforms.
        Fully defensive: intake must never fail because of this filter."""
        try:
            tag = self.env.ref('crm_lead.tag_avintiv_test', raise_if_not_found=False)
            new_vals_list = []
            for vals in vals_list:
                email = (vals.get('email_from') or '').lower()
                if email.endswith('@avintivmedia.com'):
                    vals = dict(vals)
                    vals['active'] = False
                    if tag and isinstance(vals.get('tag_ids'), list):
                        vals['tag_ids'] = vals['tag_ids'] + [(4, tag.id)]
                    _logger.info("Archived avintivmedia.com test submission: %s", email)
                new_vals_list.append(vals)
        except Exception:
            # Never let the test-domain filter break lead intake.
            _logger.exception("avintivmedia filter failed; creating leads unfiltered")
            new_vals_list = vals_list
        return super(CrmLead, self).create(new_vals_list)

    def convert_opportunity(self, partner, user_ids=False, team_id=False):
        """
        Override to queue platform conversions when Lead becomes Opportunity.
        Nothing is sent directly anymore: every event goes into a queue and
        the scheduled jobs send it with retry + failure alerts.
        """
        # 1. Perform the standard Odoo conversion first
        res = super(CrmLead, self).convert_opportunity(partner, user_ids=user_ids, team_id=team_id)

        # Suppress automatic queue triggers during the backfill script
        if self.env.context.get('is_backfill'):
            return res

        ms_queue = self.env["microsoft.ads.conversion.queue"].sudo()

        # Goal name created in Microsoft Advertising (per Avintiv); settable
        # in Settings so it can change without a deploy.
        goal_name = (self.env["ir.config_parameter"].sudo().get_param(
            "ads_integration.bing_qualified_goal_name") or "").strip() \
            or "Odoo - Qualified Lead"

        for lead in self:
            # Only process if it is now an opportunity
            if lead.type == 'opportunity':

                _logger.info(
                    "🚀 CONVERT TO OPP | Lead %s | msclkid=%s | fbclid=%s",
                    lead.id, lead.ls_msclkid, lead.ls_fbclid
                )

                # -----------------------------
                # Microsoft Ads (Bing) - Lead Event -> queue
                # -----------------------------
                raw_msclkid = lead.ls_msclkid
                clean_msclkid = _clean_msclkid(raw_msclkid)
                if clean_msclkid and not lead.ms_ads_lead_uploaded:
                    try:
                        existing = ms_queue.search([
                            ("msclkid", "=", clean_msclkid),
                            ("conversion_name", "=", goal_name),
                            ("state", "in", ("pending", "sent")),
                        ], limit=1)
                        if not existing:
                            ms_queue.create({
                                "lead_id": lead.id,
                                "msclkid": clean_msclkid,
                                "conversion_name": goal_name,
                                "conversion_time": lead.date_conversion or lead.create_date,
                                "conversion_value": lead.expected_revenue or 0.0,
                            })
                            _logger.info("Queued Bing '%s' conversion for Lead %s", goal_name, lead.id)
                    except Exception:
                        # Conversion queuing must never block the conversion itself.
                        _logger.exception("Failed to queue Bing conversion for Lead %s", lead.id)
                elif raw_msclkid and not clean_msclkid:
                    lead.message_post(body=f"Microsoft Ads Queue: Blocked invalid or short click ID '{raw_msclkid}' from being queued.")

                # -----------------------------
                # Meta Ads (Facebook) - Lead Event
                # -----------------------------
                if lead.ls_fbclid:
                    try:
                        queue = self.env["meta.ads.conversion.queue"].sudo().create({
                            "lead_id": lead.id,
                            "event_name": "Lead",
                            "event_time": lead.date_conversion or lead.create_date,
                            "value": lead.expected_revenue or 0.0,
                            "currency": lead.company_id.currency_id.name or "USD",
                            "fbclid": lead.ls_fbclid,
                        })

                        queue._send_to_meta()
                        _logger.info("✅ Meta Ads 'Lead' event sent for Lead %s", lead.id)
                    except Exception as e:
                        _logger.exception("❌ Meta conversion failed for Lead %s", lead.id)

        return res