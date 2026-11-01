from odoo import models, fields, api
from odoo.exceptions import ValidationError
import requests
import logging
import datetime
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_logger = logging.getLogger(__name__)


import re

def _clean_msclkid(value):
    """Return a valid MSCLKID (32 hex characters) or False."""
    if not value:
        return False
    val = str(value).strip().replace('-', '')
    if len(val) != 32 or val.lower() in ('organic', 'none', 'direct', 'null', 'undefined', 'n/a'):
        return False
    if not re.match(r'^[0-9a-fA-F]{32}$', val):
        return False
    return val


# =====================================================
# QUEUE MODEL
# =====================================================
class MicrosoftAdsConversionQueue(models.Model):
    _name = "microsoft.ads.conversion.queue"
    _description = "Microsoft Ads Offline Conversion Queue"
    _order = "create_date asc"
    _rec_name = "conversion_name"

    lead_id = fields.Many2one("crm.lead", ondelete="cascade", index=True)
    msclkid = fields.Char(required=True, index=True)
    conversion_name = fields.Char(required=True)
    conversion_time = fields.Datetime(required=True, default=fields.Datetime.now)
    conversion_value = fields.Float(default=0.0)
    currency_id = fields.Many2one("res.currency", default=lambda self: self.env.company.currency_id)
    state = fields.Selection([("pending", "Pending"), ("sent", "Sent"), ("failed", "Failed"), ("void", "Void")], default="pending",
                             index=True, required=True)
    is_backfill = fields.Boolean(default=False, string="Is Backfill")
    auto_send = fields.Boolean(default=True, string="Auto-Send via Scheduler",
                               help="When False, background cron will not auto-send; only sends when user clicks Send Now.")
    error_message = fields.Text()
    payload_sent = fields.Text(string="Payload Sent (JSON)", readonly=True)
    response_received = fields.Text(string="Response Received (JSON)", readonly=True)
    attempt_count = fields.Integer(default=0)
    last_attempt_at = fields.Datetime()

    _sql_constraints = [("unique_msclkid_conversion", "unique(msclkid, conversion_name)", "Already queued.")]

    MAX_ATTEMPTS = 5
    NOTIFY_AFTER_ATTEMPTS = 1

    @api.constrains("msclkid")
    def _check_msclkid(self):
        for rec in self:
            if not _clean_msclkid(rec.msclkid):
                raise ValidationError("Invalid msclkid value: %s" % rec.msclkid)

    def mark_sent(self, payload=False, response=False):
        vals = {"state": "sent", "error_message": False, "last_attempt_at": fields.Datetime.now()}
        if payload:
            vals["payload_sent"] = payload
        if response:
            vals["response_received"] = response
        self.write(vals)
        for rec in self:
            if not rec.lead_id:
                continue
            # Won goals flip the won flag; everything else counts as the
            # lead-level (qualified) event.
            is_won = "won" in (rec.conversion_name or "").lower()
            rec.lead_id.write({
                "ms_ads_won_uploaded": True if is_won else rec.lead_id.ms_ads_won_uploaded,
                "ms_ads_lead_uploaded": rec.lead_id.ms_ads_lead_uploaded if is_won else True,
                "ms_ads_last_upload_at": fields.Datetime.now(),
                "ms_ads_last_error": False,
            })
            try:
                rec.lead_id.message_post(body=f"Sent to Microsoft Ads — {rec.conversion_name} — accepted")
            except Exception:
                _logger.exception("Failed to post Microsoft conversion chatter message on lead %s", rec.lead_id.id)

    def mark_failed(self, error, payload=False, response=False):
        for rec in self:
            new_err = str(error)[:2000]
            err_msg = f"{rec.error_message}\n\n[Attempt {rec.attempt_count + 1}] {new_err}" if rec.error_message else new_err
            vals = {
                "state": "failed",
                "error_message": err_msg,
                "attempt_count": rec.attempt_count + 1,
                "last_attempt_at": fields.Datetime.now(),
            }
            if payload:
                vals["payload_sent"] = payload
            if response:
                vals["response_received"] = response
            rec.write(vals)
            # Alert whenever a failure occurs
            rec._notify_failure()
            if rec.lead_id:
                rec.lead_id.ms_ads_last_error = str(error)[:500]

    def unlink(self):
        raise models.UserError("Please void records rather than deleting them to preserve the historical log.")

    def action_void(self):
        for rec in self:
            rec.write({'state': 'void', 'auto_send': False})

    def action_reset_to_pending(self):
        """Reset records to pending and pause automatic background cron retries."""
        for rec in self:
            rec.write({
                "state": "pending",
                "error_message": False,
                "attempt_count": 0,
                "auto_send": False,
            })
            if rec.lead_id:
                is_won = "won" in (rec.conversion_name or "").lower()
                rec.lead_id.write({
                    "ms_ads_won_uploaded": False if is_won else rec.lead_id.ms_ads_won_uploaded,
                    "ms_ads_lead_uploaded": False if not is_won else rec.lead_id.ms_ads_lead_uploaded,
                    "ms_ads_last_error": False,
                })



    def _notify_failure(self):
        """Email the alert list when a conversion send fails (client asked to
        be emailed on any failure)."""
        recipients = (self.env["ir.config_parameter"].sudo().get_param(
            "ads_integration.conversion_failure_emails") or "").strip()
        if not recipients:
            _logger.warning("Microsoft Ads conversion failed (msclkid=%s, goal=%s) but no alert "
                            "email is configured (ads_integration.conversion_failure_emails).",
                            self.msclkid, self.conversion_name)
            return
        body = ("Microsoft Ads conversion send failed and will be retried.<br/><br/>"
                "Lead: %s<br/>Goal: %s<br/>MSCLKID: %s<br/>Attempts: %s<br/>Error: %s" %
                (self.lead_id.display_name if self.lead_id else "(imported row, no lead)",
                 self.conversion_name, self.msclkid or "-",
                 self.attempt_count, self.error_message or "unknown"))
        mail = self.env["mail.mail"].sudo().create({
            "subject": "[Ads Integration] Microsoft conversion send failed (%s)" % (self.msclkid or self.conversion_name),
            "email_from": self.env.company.partner_id.email or "noreply@baghouseamerica.com",
            "email_to": recipients,
            "body_html": body,
            "auto_delete": True,
        })
        try:
            mail.send(raise_exception=False)
        except Exception:
            _logger.exception("Failed to dispatch Microsoft conversion failure alert email.")


    def action_send_now(self):
        """Action to send selected queue records immediately to Microsoft Ads."""
        service = self.env["microsoft.ads.service"]
        success_count = 0
        errors = []
        for rec in self:
            payload_data = {
                "msclkid": rec.msclkid,
                "conversionName": rec.conversion_name,
                "conversionTime": str(rec.conversion_time),
                "conversionValue": rec.conversion_value,
            }
            import json as _json
            payload_str = _json.dumps(payload_data, indent=2)
            try:
                raw_response = service.send_offline_conversion(
                    msclkid=rec.msclkid,
                    conversion_name=rec.conversion_name,
                    conversion_time=rec.conversion_time,
                    conversion_value=rec.conversion_value,
                )
                rec.mark_sent(payload=payload_str, response=raw_response)
                success_count += 1
            except Exception as e:
                _logger.exception("❌ Bing conversion failed for lead %s", rec.lead_id.id if rec.lead_id else rec.msclkid)
                rec.mark_failed(str(e), payload=payload_str, response=str(e))
                name = rec.lead_id.display_name if rec.lead_id else rec.msclkid
                errors.append(f"{name}: {str(e)}")

        if errors and not success_count:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Microsoft Ads Send Failed',
                    'message': '; '.join(errors[:3]),
                    'type': 'danger',
                    'sticky': True,
                }
            }
        elif errors:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Partial Success',
                    'message': f"{success_count} sent successfully. Errors: {'; '.join(errors[:2])}",
                    'type': 'warning',
                    'sticky': True,
                }
            }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Success',
                    'message': f"{success_count} conversion(s) sent successfully to Microsoft Ads!",
                    'type': 'success',
                    'sticky': False,
                }
            }

    @api.model
    def send_pending_conversions(self, limit=25):
        service = self.env["microsoft.ads.service"]
        ICP = self.env["ir.config_parameter"].sudo()
        if not ICP.get_param("ads_integration.bing_refresh_token"):
            _logger.info("Microsoft Ads conversion queue skipped: no refresh token configured.")
            return
        records = self.search([
            "|",
            "&", ("state", "=", "pending"), ("attempt_count", "=", 0),
            "&", ("state", "=", "failed"), ("attempt_count", "<", self.MAX_ATTEMPTS),
            ("auto_send", "=", True),
        ], limit=limit)

        for rec in records:
            payload_data = {
                "msclkid": rec.msclkid,
                "conversionName": rec.conversion_name,
                "conversionTime": str(rec.conversion_time),
                "conversionValue": rec.conversion_value,
            }
            import json as _json
            payload_str = _json.dumps(payload_data, indent=2)
            try:
                raw_response = service.send_offline_conversion(
                    msclkid=rec.msclkid,
                    conversion_name=rec.conversion_name,
                    conversion_time=rec.conversion_time,
                    conversion_value=rec.conversion_value,
                )
                rec.mark_sent(payload=payload_str, response=raw_response)
            except Exception as e:
                err_str = str(e)
                _logger.exception("❌ Bing conversion failed: %s", err_str)
                rec.mark_failed(err_str, payload=payload_str, response=err_str)
                if any(w in err_str.lower() for w in ('aadsts', 'unauthorized', 'invalid_grant', 'oauth', 'token')):
                    _logger.warning("Aborting Microsoft Ads queue drain due to OAuth/token error: %s", err_str)
                    break




# =====================================================
# MICROSOFT ADS SERVICE
# =====================================================
class MicrosoftAdsService(models.AbstractModel):
    _name = "microsoft.ads.service"
    _description = "Microsoft Ads API Service"

    def _get_access_token(self):
        """
        Retrieves Access Token and ROTATES Refresh Token using 'ads_integration' params.
        """
        ICP = self.env["ir.config_parameter"].sudo()

        # ✅ UPDATED: Now pulling from your specific 'ads_integration' fields
        client_id = (ICP.get_param("ads_integration.bing_client_id") or "").strip()
        client_secret = (ICP.get_param("ads_integration.bing_client_secret") or "").strip()
        refresh_token = (ICP.get_param("ads_integration.bing_refresh_token") or "").strip()
        tenant_id = (ICP.get_param("ads_integration.bing_tenant_id") or "").strip()  # Now dynamic from settings

        if not refresh_token:
            raise ValidationError(
                "Bing Error: No Refresh Token found in Settings (ads_integration.bing_refresh_token).")

        if not tenant_id:
            # Fallback only if tenant ID is missing in settings (safety net)
            tenant_id = "1b7d5eac-f271-437a-a59d-122e584f469f"

        token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": "https://ads.microsoft.com/msads.manage offline_access",
        }

        try:
            response = requests.post(token_url, data=payload, timeout=20)
            data = response.json()
            if response.status_code != 200:
                raise Exception(f"Auto-Login Failed: {data.get('error_description')}")

            # ✅ Token Rotation: Updates the field in your Settings Page
            new_refresh_token = data.get("refresh_token")
            if new_refresh_token:
                ICP.set_param("ads_integration.bing_refresh_token", new_refresh_token)
                _logger.info("✅ Bing Refresh Token successfully rotated in Settings.")

            return data.get("access_token")

        except Exception as e:
            _logger.exception("Bing OAuth Connection Error")
            raise e

    def send_offline_conversion(self, msclkid, conversion_name, conversion_time, conversion_value, currency_code="USD"):
        ICP = self.env["ir.config_parameter"].sudo()

        from xml.sax.saxutils import escape

        access_token = escape(self._get_access_token() or "")
        developer_token = escape((ICP.get_param("ads_integration.bing_developer_token") or "").strip())
        account_id = escape((ICP.get_param("ads_integration.bing_account_id") or "").strip())
        customer_id = escape((ICP.get_param("ads_integration.bing_customer_id") or "").strip())
        msclkid = escape(msclkid or "")
        conv_name = escape(conversion_name or "")
        conv_val = escape(str(conversion_value or "0.0"))
        currency = escape(currency_code or "USD")

        # ✅ Date Format Fix
        if isinstance(conversion_time, str):
            clean_time = conversion_time.replace(" ", "T")
        else:
            clean_time = conversion_time.strftime("%Y-%m-%dT%H:%M:%S")

        soap_envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
    <s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
      <s:Header>
        <h:AuthenticationToken xmlns:h="https://bingads.microsoft.com/CampaignManagement/v13">{access_token}</h:AuthenticationToken>
        <h:DeveloperToken xmlns:h="https://bingads.microsoft.com/CampaignManagement/v13">{developer_token}</h:DeveloperToken>
        <h:CustomerAccountId xmlns:h="https://bingads.microsoft.com/CampaignManagement/v13">{account_id}</h:CustomerAccountId>
        <h:CustomerId xmlns:h="https://bingads.microsoft.com/CampaignManagement/v13">{customer_id}</h:CustomerId>
      </s:Header>
      <s:Body>
        <ApplyOfflineConversionsRequest xmlns="https://bingads.microsoft.com/CampaignManagement/v13">
          <OfflineConversions>
            <OfflineConversion>
              <ConversionCurrencyCode>{currency}</ConversionCurrencyCode>
              <ConversionName>{conv_name}</ConversionName>
              <ConversionTime>{clean_time}</ConversionTime>
              <ConversionValue>{conv_val}</ConversionValue>
              <MicrosoftClickId>{msclkid}</MicrosoftClickId>
            </OfflineConversion>
          </OfflineConversions>
        </ApplyOfflineConversionsRequest>
      </s:Body>
    </s:Envelope>"""

        url = "https://campaign.api.bingads.microsoft.com/Api/Advertiser/CampaignManagement/v13/CampaignManagementService.svc"
        
        # We explicitly encode to bytes to ensure requests computes the exact Content-Length for the WCF endpoint
        soap_bytes = soap_envelope.encode("utf-8")
        headers = {
            "Content-Type": "text/xml; charset=utf-8", 
            "SOAPAction": "ApplyOfflineConversions",
            "Content-Length": str(len(soap_bytes))
        }

        _logger.info("📤 Sending Bing SOAP request to Account: %s | Cust: %s", account_id, customer_id)

        response = requests.post(url, data=soap_bytes, headers=headers, timeout=30)

        if response.status_code == 200:
            if "Fault" in response.text:
                _logger.error("❌ Bing SOAP Fault: %s", response.text)
                raise Exception(f"Bing SOAP Fault: {response.text}")
            
            # Ben's exact request: check for PartialErrors even on HTTP 200
            import re
            partial_error_match = re.search(r'<PartialErrors[^>]*>(.*?)</PartialErrors>', response.text, re.DOTALL)
            if partial_error_match and "BatchError" in partial_error_match.group(1):
                _logger.error("❌ Bing PartialError: %s", response.text)
                raise Exception(f"PartialError from Microsoft Ads: {response.text}")
            
            _logger.info("✅ Bing Offline Conversion sent successfully")
            return response.text
        else:
            _logger.error("❌ Bing API Failed: %s", response.text)
            raise Exception(f"Bing API Error: {response.text}")