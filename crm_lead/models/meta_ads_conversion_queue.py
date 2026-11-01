from odoo import models, fields, api
import logging
import time
import uuid
import requests

_logger = logging.getLogger(__name__)

class MetaAdsConversionQueue(models.Model):
    _name = "meta.ads.conversion.queue"
    _description = "Meta Ads Conversion Queue"
    _order = "create_date asc"

    lead_id = fields.Many2one(
        "crm.lead",
        string="Lead / Opportunity",
        required=True,
        ondelete="cascade",
    )

    event_name = fields.Char(
        required=True,
        default="Purchase",
        help="Meta standard event name (e.g., Purchase, Lead, CompleteRegistration)",
    )

    event_time = fields.Datetime(
        required=True,
        help="UTC time when conversion happened",
    )

    value = fields.Float(
        string="Conversion Value",
        default=0.0,
    )

    currency = fields.Char(
        default="USD",
    )

    fbclid = fields.Char(
        string="Facebook Click ID",
        index=True,
    )

    status = fields.Selection(
        [
            ("pending", "Pending"),
            ("sent", "Sent"),
            ("failed", "Failed"),
        ],
        default="pending",
        index=True,
    )

    error_message = fields.Text()
    sent_at = fields.Datetime()

    def _send_to_meta(self):
        ICP = self.env['ir.config_parameter'].sudo()

        # ✅ UPDATED: Fetch parameters from your new 'ads_integration' settings
        PIXEL_ID = ICP.get_param('ads_integration.meta_pixel_id')
        ACCESS_TOKEN = ICP.get_param('ads_integration.meta_access_token')
        TEST_EVENT_CODE = ICP.get_param('ads_integration.meta_test_event_code')  # optional

        if not PIXEL_ID or not ACCESS_TOKEN:
            _logger.error("❌ Meta CAPI not configured: missing pixel_id or access_token in Settings")
            return

        url = f"https://graph.facebook.com/v19.0/{PIXEL_ID}/events"

        # Process pending records
        records = self.search([('status', '=', 'pending')], limit=10)

        for rec in records:
            try:
                # Convert Odoo datetime to Unix Timestamp
                event_time = int(time.mktime(rec.event_time.timetuple()))

                # Prepare Payload
                payload = {
                    "data": [{
                        "event_name": rec.event_name,
                        "event_time": event_time,
                        "event_id": f"odoo-{rec.lead_id.id}-{uuid.uuid4()}",
                        "action_source": "website",
                        "custom_data": {
                            "value": float(rec.value or 0.0),
                            "currency": rec.currency or "USD",
                        },
                        "user_data": {
                            # Format required by Meta: fb.1.timestamp.fbclid
                            "fbc": f"fb.1.{int(time.time())}.{rec.fbclid}",
                        }
                    }]
                }

                # Enable Meta Test Events if configured in settings
                if TEST_EVENT_CODE:
                    payload["test_event_code"] = TEST_EVENT_CODE

                _logger.info("➡️ Sending Meta CAPI payload for lead %s", rec.lead_id.id)

                response = requests.post(
                    url,
                    json=payload,
                    params={"access_token": ACCESS_TOKEN},
                    timeout=20
                )

                if response.status_code == 200:
                    rec.write({
                        'status': 'sent',
                        'sent_at': fields.Datetime.now(),
                        'error_message': False,
                    })
                    _logger.info("✅ Meta CAPI success for lead %s", rec.lead_id.id)
                else:
                    rec.write({
                        'status': 'failed',  # ✅ Fixed: was 'error' (invalid selection)
                        'error_message': response.text,
                    })
                    _logger.error("❌ Meta CAPI failed: %s", response.text)

            except Exception as e:
                rec.write({
                    'status': 'failed',  # ✅ Fixed: was 'error'
                    'error_message': str(e),
                })
                _logger.exception("🔥 Exception while sending Meta CAPI for lead %s", rec.lead_id.id)