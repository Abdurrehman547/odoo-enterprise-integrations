# -*- coding: utf-8 -*-
from odoo import fields, models


class DeliveryCarrier(models.Model):
    _inherit = "delivery.carrier"

    synapse_service_code = fields.Char(
        string="Synapse Service Code",
        help="Short code sent to Synapse for this shipping method (create-order "
        "'delivery_service', max 4 characters), e.g. the courier service code. "
        "If empty, the carrier name is sent instead (truncated to fit). Using a "
        "real code avoids sending a meaningless truncated name.",
    )
    synapse_carrier_code = fields.Char(
        string="Synapse Carrier Code",
        help="Carrier code sent to Synapse for this shipping method (create-order "
        "'carrier', max 10 characters), e.g. UPS / FEDEX / an SCAC. This is the "
        "transport company, distinct from the service level (Synapse Service "
        "Code). Leave empty to send no carrier. Confirm the valid codes with "
        "your Synapse / FrPRT admin.",
    )

