from odoo import http
from odoo.http import request
import logging
import json

import re

_logger = logging.getLogger(__name__)


def _clean_gclid(value):
    """Return a plausible GCLID, or False."""
    if not value:
        return False
    val = str(value).strip()
    if len(val) < 40 or val.lower() in ('organic', 'none', 'direct', 'null', 'undefined', 'n/a', '-'):
        return False
    if not (val.startswith('Cj0') or val.startswith('CjwK') or val.startswith('EAIa') or val.startswith('Cj') or val.startswith('EA')):
        return False
    return val


def _clean_msclkid(value):
    """Return a valid MSCLKID (32 hex characters) or False."""
    if not value:
        return False
    val = str(value).strip()
    if len(val) != 32 or val.lower() in ('organic', 'none', 'direct', 'null', 'undefined', 'n/a', '-'):
        return False
    if not re.match(r'^[0-9a-fA-F]{32}$', val):
        return False
    return val


class CrmLeadController(http.Controller):

    @http.route('/create/crm/lead', type='http', auth='public', methods=['POST'], csrf=False, website=True)
    def create_crm_lead(self, **post):
        try:
            _logger.info("Incoming lead data: %s", post)

            # ===== COMPANY IDS =====
            IBA_COMPANY_ID = 2
            BAGHOUSE_COMPANY_ID = 1  # <-- Put correct Baghouse ID here

            # ===== DECIDE COMPANY BASED ON lead_source =====
            source = (post.get('lead_source') or '').strip()

            if source == "IBA":
                company_id = IBA_COMPANY_ID
            else:
                # DEFAULT = BAGHOUSE
                company_id = BAGHOUSE_COMPANY_ID

            # ===== FORCE COMPANY CONTEXT (CRITICAL) =====
            env = request.env(context=dict(
                request.env.context,
                allowed_company_ids=[company_id],
                force_company=company_id,
            ))

            # ===== VALIDATION =====
            if not post.get('name') or not post.get('email'):
                return json.dumps({"status": "error", "message": "Missing name/email"})

            # ===== CREATE / FIND PARTNER IN CORRECT COMPANY =====
            main_partner, contact_partner = self.create_company_and_contact(env, {
                "company_name": post.get('company'),
                "name": post.get('name'),
                "email": post.get('email'),
                "phone": post.get('phone', ''),
                "company_id": company_id,
            })

            # ===== UTM ATTRIBUTION =====
            # The website posts utm_* as plain strings; crm.lead expects m2o
            # records, so resolve (or create) them here. Odoo's own utm.mixin
            # cookie fallback never fires because the form is submitted
            # server-to-server from WordPress, with no Odoo session.
            utm_source_str = (post.get('utm_source') or '').strip()
            if not utm_source_str or utm_source_str.lower() in ('direct', 'none'):
                utm_source_str = 'Direct Traffic'

            utm_source = self.get_or_create_utm(env, 'utm.source', utm_source_str)
            if not utm_source:
                utm_source = self.resolve_utm_id(env, 'utm.source', post.get('source_id'))
            if not utm_source:
                utm_source = self.get_or_create_utm(env, 'utm.source', 'Direct Traffic')

            # ===== CLEAN GCLID & MSCLKID =====
            raw_gclid = (post.get('gclid') or '').strip()
            raw_msclkid = (post.get('msclkid') or '').strip()

            clean_gclid = _clean_gclid(raw_gclid)
            clean_msclkid = _clean_msclkid(raw_msclkid)

            utm_medium_val = post.get('utm_medium', '')
            if not clean_gclid and raw_gclid.lower() == 'organic' and not utm_medium_val:
                utm_medium_val = 'organic'

            # ===== LEAD VALUES =====
            lead_vals = {
                "company_id": company_id,
                "partner_id": main_partner.id if main_partner else False,
                "contact_name": contact_partner.name if contact_partner else post.get('name'),
                "partner_name": main_partner.name if main_partner and main_partner.is_company else False,
                "type": "lead",
                "name": post.get("subject") or f"New Lead: {post.get('name')}",
                "email_from": post.get('email'),
                "phone": post.get('phone', ''),
                "description": post.get('description', ''),
                "referred": source or "Baghouse",  # store default value
                "ls_gclid": clean_gclid or '',
                "ls_msclkid": clean_msclkid or '',
                "ls_fbclid": post.get('fbclid', ''),

                # UTM many2one fields (what the CRM reports on)
                "source_id": utm_source,
                "medium_id": self.get_or_create_utm(env, 'utm.medium', utm_medium_val),
                "campaign_id": self.get_or_create_utm(env, 'utm.campaign', post.get('utm_campaign')),

                # Raw strings, kept for audit / debugging
                "ls_utm_source": utm_source_str,
                "ls_utm_medium": utm_medium_val,
                "ls_utm_campaign": post.get('utm_campaign', ''),
                "ls_utm_content": post.get('utm_content', ''),
            }

            # ===== CREATE LEAD =====
            lead = env['crm.lead'].sudo().with_company(company_id).create(lead_vals)

            _logger.info(
                "Lead created in %s (Company ID %s) - Lead ID %s",
                "IBA" if company_id == IBA_COMPANY_ID else "Baghouse",
                company_id,
                lead.id
            )

            return json.dumps({
                "status": "success",
                "lead_id": lead.id,
                "company_id": company_id
            })

        except Exception as e:
            _logger.exception("Lead Creation Error: %s", str(e))
            return json.dumps({"status": "error", "message": str(e)})

    # ==========================================================
    # UTM Resolution
    # ==========================================================

    def get_or_create_utm(self, env, model, name):
        """Resolve a utm.source / utm.medium / utm.campaign by name.

        Creates the record when it does not exist yet, so attribution from a
        new campaign is never silently dropped. Never raises: a UTM lookup
        failure must not stop the lead itself from being created.
        """
        if not isinstance(name, str):
            return False
        name = name.strip()
        if not name:
            return False

        Utm = env[model].sudo()
        record = Utm.search([('name', '=ilike', name)], limit=1)
        if record:
            return record.id

        # The create runs in a savepoint: a failed INSERT aborts the whole
        # transaction, which would take the lead down with it. The savepoint
        # keeps the cursor usable so the retry below can run.
        try:
            with env.cr.savepoint():
                return Utm.create({'name': name}).id
        except Exception:
            # Most likely a unique-name race with a concurrent submission.
            _logger.warning("Could not create %s '%s'", model, name, exc_info=True)

        record = Utm.search([('name', '=ilike', name)], limit=1)
        return record.id if record else False

    def resolve_utm_id(self, env, model, value):
        """Accept a raw database id, but only if it still points at a record."""
        try:
            record_id = int(value)
        except (TypeError, ValueError):
            return False

        record = env[model].sudo().browse(record_id).exists()
        if not record:
            _logger.warning("%s id %s sent by the website does not exist", model, record_id)
            return False
        return record.id

    # ==========================================================
    # Partner Creation (Company Safe)
    # ==========================================================

    def create_company_and_contact(self, env, data):
        Partner = env['res.partner'].sudo().with_company(data.get('company_id'))
        cid = data.get('company_id')

        company_partner = False
        company_name = data.get('company_name')

        # ---- Company Partner ----
        if company_name:
            company_partner = Partner.search([
                ('name', '=', company_name),
                ('is_company', '=', True),
                ('company_id', '=', cid)
            ], limit=1)

            if not company_partner:
                company_partner = Partner.create({
                    'name': company_name,
                    'is_company': True,
                    'company_id': cid,
                })

        # ---- Contact Partner ----
        # Search among persons only ('is_company', '=', False) and never on an
        # empty value: otherwise the search can return the company created
        # above, and the write below would set it as its own parent.
        contact_partner = False
        if data.get('email'):
            contact_partner = Partner.search([
                ('email', '=', data.get('email')),
                ('company_id', '=', cid),
                ('is_company', '=', False),
            ], limit=1)
        elif data.get('name'):
            contact_partner = Partner.search([
                ('name', '=', data.get('name')),
                ('company_id', '=', cid),
                ('is_company', '=', False),
            ], limit=1)

        if not contact_partner:
            contact_partner = Partner.create({
                'name': data.get('name'),
                'email': data.get('email'),
                'phone': data.get('phone'),
                'parent_id': company_partner.id if company_partner else False,
                'company_id': cid,
                'type': 'contact',
            })
        elif company_partner and not contact_partner.parent_id and contact_partner != company_partner:
            contact_partner.write({'parent_id': company_partner.id})

        main_entity = company_partner if company_partner else contact_partner
        return main_entity, contact_partner