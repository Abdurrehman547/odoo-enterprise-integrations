# -*- coding: utf-8 -*-
import base64
import logging
from datetime import datetime, timedelta

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60
DATE_FMT = "%m/%d/%Y"
DATETIME_FMT = "%m/%d/%Y %H:%M"

# Synapse order_type codes. NOTE two different sets exist:
#   * create-order only ACCEPTS the regex /A|C|I|P|Q|R|T|U|O|V|F/i.
#   * the Synapse UI order-type list is larger and also has K=KitWorkOrder,
#     M=Misc Order, S=Store Renew, W=Work Order — these exist as types but
#     create-order REJECTS them (API-00097), so they are NOT offered here.
# Descriptions below come from the Synapse UI (confirmed). I and F are accepted
# by create-order but absent from the UI list, so their meaning is unconfirmed.
# Direction is confirmed live: Outbound types require from_facility; inbound /
# receipt types require to_facility (transfers T/U need both).
SYNAPSE_ORDER_TYPES = [
    # --- Outbound: goods leave the facility (require From Facility) ---
    ("O", "O — Outbound (use for sales orders)"),
    ("V", "V — ReturnVendor (goods returned to a vendor)"),
    ("T", "T — Transfer (needs From + To Facility)"),
    ("U", "U — Cross Cust (cross-customer)"),
    ("F", "F — Outbound type F (not in Synapse UI list; confirm with FrPRT)"),
    # --- Inbound / receipt: goods arrive at the facility (require To Facility) ---
    ("R", "R — Receipt"),
    ("A", "A — Master Rcpt (master receipt)"),
    ("C", "C — Transload/CD (transload / cross-dock)"),
    ("P", "P — Production"),
    ("Q", "Q — Returns"),
    ("I", "I — Inbound type I (not in Synapse UI list; confirm with FrPRT)"),
]

# Inbound-only subset offered for purchase orders (warehouse imports). Same
# create-order endpoint; these types REQUIRE to_facility (goods arrive at the
# facility) and get no from_facility. 'R' (Receipt) is the default — the
# standard inbound type in the Synapse UI. 'I' was removed: Synapse accepted
# it but treated those orders as outbound.
SYNAPSE_INBOUND_ORDER_TYPES = [
    ("R", "R — Receipt"),
    ("A", "A — Master Rcpt (master receipt)"),
    ("C", "C — Transload/CD (transload / cross-dock)"),
    ("P", "P — Production"),
    ("Q", "Q — Returns"),
]

# Synapse order_status codes (from the Synapse UI; reported by order-info /
# order-detail as `order_status`). Used to surface the real WMS status on the SO.
SYNAPSE_ORDER_STATUSES = [
    ("0", "Hold"),
    ("1", "Entered"),
    ("2", "Committed"),
    ("3", "Planned"),
    ("4", "Released"),
    ("5", "Picking"),
    ("6", "Picked"),
    ("7", "Loading"),
    ("8", "Loaded"),
    ("9", "Shipped"),
    ("A", "Arrived"),
    ("R", "Received"),
    ("X", "Cancelled"),
]

# Synapse order_priority codes (from the Synapse UI). Sent on create-order in
# the 1-char `priority` header field.
SYNAPSE_ORDER_PRIORITIES = [
    ("0", "Hot"),
    ("A", "Normal"),
    ("B", "Back Order"),
    ("D", "Drop Ship"),
    ("E", "Exception"),
    ("N", "No Notify"),
    ("S", "SameDayShip"),
]

# Maximum length Synapse allows for each create-order *header* field (from the
# API schema). Values longer than this fail with "API-00097 '<field>' length
# must be less than or equal to N characters long", so every string we send is
# truncated to its limit. NOTE the carrier/shipping fields are short *codes*:
# carrier=10, delivery_service=4, ship_type=1 — an Odoo carrier *name* will not
# fit and should be mapped to a real Synapse code (see _order_shipping_service).
SYNAPSE_HEADER_MAXLEN = {
    "custid": 10, "order_type": 1, "po_number": 20, "reference": 20,
    "rma": 20, "prono": 20, "from_facility": 3, "to_facility": 3,
    "ship_to": 10, "bol": 40, "priority": 1, "shipper": 10, "consignee": 10,
    "ship_type": 1, "carrier": 10, "shipment_terms": 3, "delivery_service": 4,
    "saturday_delivery": 1, "cod": 1, "bill_to_account": 50,
    "special_service_1": 4, "special_service_2": 4, "special_service_3": 4,
    "special_service_4": 4, "import_file_id": 255, "rf_auto_display": 1,
    "validate_shipto": 1, "create_order_with_empty_item_yn": 1,
    "ship_to_name": 40, "ship_to_contact": 40, "ship_to_address_1": 40,
    "ship_to_address_2": 40, "ship_to_city": 30, "ship_to_state": 5,
    "ship_to_postal_code": 12, "ship_to_country_code": 3, "ship_to_phone": 25,
    "ship_to_fax": 25, "ship_to_email": 255,
    "shipper_name": 40, "shipper_contact": 40, "shipper_address_1": 40,
    "shipper_address_2": 40, "shipper_city": 30, "shipper_state": 5,
    "shipper_postal_code": 12, "shipper_country_code": 3, "shipper_phone": 15,
    "shipper_fax": 15, "shipper_email": 255,
    "bill_to_name": 40, "bill_to_contact": 40, "bill_to_address_1": 40,
    "bill_to_address_2": 40, "bill_to_city": 30, "bill_to_state": 5,
    "bill_to_postal_code": 12, "bill_to_country_code": 3, "bill_to_phone": 25,
    "bill_to_fax": 25, "bill_to_email": 255,
}

# Maximum length for create-order *detail* (line) fields we send.
SYNAPSE_DETAIL_MAXLEN = {"uom_entered": 4, "consignee_sku": 20, "lot_number": 30}

# Maximum length for the orders/update-order *header* fields we send. NOTE
# update-order is header-only: it can change carrier / shipping / routing
# fields but has NO details array, so it CANNOT change line items, quantities,
# or the ship-to address. Content/address changes require a resync (cancel +
# recreate) instead (see SynapseConnection.resync_order).
SYNAPSE_UPDATE_MAXLEN = {
    "carrier": 10, "prono": 20, "tms_shipment_id": 20,
    "routingstatus": 12, "deliveryservice": 4,
}

# Remediation hints appended to specific Synapse API error codes so the user
# sees what to fix in Odoo instead of an opaque API code (see _format_error).
SYNAPSE_ERROR_HINTS = {
    "API-00069": (
        "The 'delivery_service' code sent is not one Synapse recognises. Set "
        "the 'Synapse Service Code' on the order's delivery method (Inventory "
        "→ Configuration → Delivery Methods, Synapse tab) to a valid "
        "4-character Synapse code, or correct the Default Shipping Service on "
        "the Synapse connection."
    ),
}

# Auto-derive the create-order ``carrier`` field (transport company, max 10)
# when a shipping method has no explicit ``synapse_carrier_code``. Most BHA
# methods are delivery_type='fixed' with the carrier only in the name
# ("UPS Ground collect to ...", "Fedex Next Day Air ..."), so we detect it from
# the name. ``delivery_type`` is checked first for the few real integrations.
# Keywords are matched case-insensitively; order matters (most specific first).
# NOTE: these are the common carrier identifiers — confirm/replace them with the
# exact ``carrier`` codes your Synapse / FrPRT facility accepts.
SYNAPSE_CARRIER_BY_DELIVERY_TYPE = {
    "ups": "UPS", "usps": "USPS", "fedex": "FEDEX", "dhl": "DHL",
}
SYNAPSE_CARRIER_NAME_KEYWORDS = [
    ("fed ex", "FEDEX"), ("fedex", "FEDEX"),
    ("ups", "UPS"),
    ("usps", "USPS"), ("postal", "USPS"), ("u.s.p.s", "USPS"),
    ("dhl", "DHL"),
]


class SynapseConnection(models.Model):
    _name = "synapse.connection"
    _description = "Synapse WMS Connection"

    name = fields.Char(string="Connection Name", required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", string="Company", default=lambda self: self.env.company
    )

    base_url = fields.Char(
        string="Base URL",
        required=True,
        default="https://frprt.synapsewms.net/test/api",
        help="Synapse API base URL, e.g. https://frprt.synapsewms.net/test/api",
    )
    auth_type = fields.Selection(
        [
            ("bearer", "Bearer Token"),
            ("apikey", "x-auth-token"),
            ("session", "Username / Password (session)"),
        ],
        string="Authentication",
        default="bearer",
        required=True,
    )
    token = fields.Char(
        string="API Token",
        help="Bearer token or x-auth-token value, depending on the selected "
        "authentication mode.",
        copy=False,
    )
    username = fields.Char(string="Username", help="Used for session authentication.")
    password = fields.Char(string="Password", help="Used for session authentication.")
    session_cookie = fields.Char(string="Session Cookie", readonly=True, copy=False)

    # Synapse tenant scope
    custid = fields.Char(
        string="Customer ID",
        required=True,
        default="BAGAME",
        help="Synapse customer id (custid) this connection is scoped to.",
    )
    facility = fields.Char(
        string="Facility",
        required=True,
        default="04",
        help="Synapse facility code.",
    )

    # Order defaults
    default_order_type = fields.Selection(
        SYNAPSE_ORDER_TYPES,
        string="Default Order Type",
        default="O",
        help="Synapse order_type used when creating orders. 'O' is the standard "
        "outbound shipping order for a sales order. Outbound types (O/V/F/T/U) "
        "require a From Facility; inbound types (A/C/I/P/Q/R/T/U) require a To "
        "Facility.",
    )
    default_priority = fields.Selection(
        SYNAPSE_ORDER_PRIORITIES,
        string="Default Priority",
        help="Synapse order priority sent on create-order (header 'priority'). "
        "Leave empty to send none.",
    )
    default_uom = fields.Char(
        string="Default UoM",
        default="EA",
        help="Synapse unit-of-measure code sent on order lines when a product "
        "specific mapping is not available.",
    )
    from_facility = fields.Char(
        string="From Facility",
        help="from_facility sent on create-order (required for some order types).",
    )
    to_facility = fields.Char(
        string="To Facility",
        help="to_facility sent on create-order (required for some order types).",
    )
    default_inbound_order_type = fields.Selection(
        SYNAPSE_INBOUND_ORDER_TYPES,
        string="Default Inbound Order Type",
        default="R",
        help="Synapse order_type used when sending purchase orders (inbound / "
        "warehouse imports). Inbound types require a To Facility. 'R' "
        "(Receipt) is the standard Synapse inbound type and the confirmed "
        "default; 'I' is accepted by the API but not shown in the Synapse UI "
        "(Synapse treated it as outbound).",
    )
    inbound_warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Inbound Warehouse",
        help="Warehouse whose Address partner is sent as the deliver-to on "
        "inbound (purchase) orders — e.g. 'Baghouse America - AZ' (its "
        "Receipts dock). Goods are received into this warehouse's facility.",
    )
    inbound_to_facility = fields.Char(
        string="Inbound To Facility",
        help="Synapse to_facility code inbound (purchase) orders receive into "
        "(required for inbound order types). Falls back to To Facility, then "
        "Facility.",
    )
    shipping_service_key = fields.Char(
        string="Shipping Service Field",
        default="delivery_service",
        help="Name of the create-order header key used to send the shipping "
        "service / carrier (confirm the exact key with your Synapse admin).",
    )
    default_shipping_service = fields.Char(
        string="Default Shipping Service",
        help="Fallback shipping service / carrier code sent on create-order "
        "when the sale order has no delivery carrier set.",
    )

    # Inventory behaviour
    auto_reconcile_inventory = fields.Boolean(
        string="Auto Reconcile Inventory",
        default=False,
        help="When enabled, pulling inventory will adjust Odoo on-hand quantities "
        "to match Synapse. Disabled by default: a snapshot is stored for review "
        "without mutating stock.",
    )
    inventory_location_id = fields.Many2one(
        "stock.location",
        string="Inventory Location",
        domain="[('usage', '=', 'internal')]",
        help="Odoo location used when reconciling Synapse on-hand quantities.",
    )

    # Packing slip automation
    auto_send_packing_slip = fields.Boolean(
        string="Auto Send Packing Slip",
        default=False,
        help="When enabled, automatically generate and upload the packing slip "
        "(delivery slip) to Synapse Anywhere after an order has been sent to "
        "Synapse.",
    )
    auto_validate_delivery = fields.Boolean(
        string="Auto Validate Delivery on Shipped",
        default=False,
        help="When enabled, pulling a 'Shipped' order from Synapse automatically sets "
        "delivered quantities on the Odoo delivery order (stock picking), validates it, "
        "and creates a backorder for any unfulfilled items.",
    )

    # Sync bookkeeping
    last_inventory_sync = fields.Datetime(string="Last Inventory Sync", readonly=True)
    last_shipment_sync = fields.Datetime(string="Last Shipment Sync", readonly=True)

    # ------------------------------------------------------------------
    # Low level API communication layer
    # ------------------------------------------------------------------
    def _headers(self):
        """Build request headers for the configured authentication mode."""
        self.ensure_one()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.auth_type == "bearer" and self.token:
            headers["Authorization"] = "Bearer %s" % self.token
        elif self.auth_type == "apikey" and self.token:
            headers["x-auth-token"] = self.token
        elif self.auth_type == "session" and self.session_cookie:
            headers["Cookie"] = "SYNAPSE-SESSION=%s" % self.session_cookie
        return headers

    def _url(self, endpoint):
        return "%s/%s" % (self.base_url.rstrip("/"), endpoint.lstrip("/"))

    def _call(self, endpoint, payload=None, raw=False, _retry=True):
        """Perform a POST against the Synapse API.

        :param endpoint: path relative to base_url, e.g. ``orders/create-order``
        :param payload: dict serialised as the JSON body
        :param raw: when True return the raw ``bytes`` body (binary downloads)
        :returns: parsed JSON (dict/list) or ``bytes`` when ``raw``
        """
        self.ensure_one()
        url = self._url(endpoint)
        headers = self._headers()
        _logger.info("Synapse -> POST %s payload=%s", url, payload)
        try:
            response = requests.post(
                url, json=payload or {}, headers=headers, timeout=DEFAULT_TIMEOUT
            )
        except requests.RequestException as exc:
            _logger.exception("Synapse: request to %s failed", url)
            raise UserError(_("Could not reach Synapse at %s:\n%s") % (url, exc))

        # Session expiry: re-login once and retry.
        if (
            response.status_code in (401, 403)
            and self.auth_type == "session"
            and _retry
        ):
            self._login()
            return self._call(endpoint, payload=payload, raw=raw, _retry=False)

        if response.status_code >= 400:
            raise UserError(self._format_error(response))

        if raw:
            return response.content
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    def _call_multipart(self, endpoint, data=None, files=None, _retry=True):
        """POST ``multipart/form-data`` (binary file uploads).

        Auth headers are sent, but NOT Content-Type — ``requests`` sets the
        multipart boundary itself. ``data`` are the form fields, ``files`` the
        file parts in ``requests`` ``{name: (filename, bytes, mimetype)}`` form.
        """
        self.ensure_one()
        url = self._url(endpoint)
        headers = self._headers()
        headers.pop("Content-Type", None)  # let requests set the boundary
        _logger.info(
            "Synapse -> POST(multipart) %s fields=%s files=%s",
            url, list((data or {}).keys()), list((files or {}).keys()),
        )
        try:
            response = requests.post(
                url, data=data or {}, files=files or {},
                headers=headers, timeout=DEFAULT_TIMEOUT,
            )
        except requests.RequestException as exc:
            _logger.exception("Synapse: multipart request to %s failed", url)
            raise UserError(_("Could not reach Synapse at %s:\n%s") % (url, exc))

        if (
            response.status_code in (401, 403)
            and self.auth_type == "session"
            and _retry
        ):
            self._login()
            return self._call_multipart(
                endpoint, data=data, files=files, _retry=False
            )
        if response.status_code >= 400:
            raise UserError(self._format_error(response))
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    @staticmethod
    def _format_error(response):
        """Turn a Synapse error response into a readable message."""
        message = "%s %s" % (response.status_code, response.reason)
        code = None
        try:
            data = response.json()
            err = data.get("error") if isinstance(data, dict) else None
            if isinstance(err, dict):
                code = err.get("errorCode")
                message = "%s (%s)" % (
                    err.get("message", message),
                    code or response.status_code,
                )
            elif isinstance(data, dict) and data.get("message"):
                message = data["message"]
        except ValueError:
            if response.text:
                message = response.text[:500]
        hint = SYNAPSE_ERROR_HINTS.get(code)
        if hint:
            message = "%s\n\n%s" % (message, hint)
        return _("Synapse API error: %s") % message

    def _login(self):
        """Session authentication: POST /login and store the session cookie."""
        self.ensure_one()
        if not (self.username and self.password):
            raise UserError(_("Username and password are required for session login."))
        url = self._url("login")
        try:
            response = requests.post(
                url,
                json={"username": self.username, "password": self.password},
                headers={"Content-Type": "application/json"},
                timeout=DEFAULT_TIMEOUT,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise UserError(_("Synapse login failed:\n%s") % exc)
        cookie = response.cookies.get("SYNAPSE-SESSION")
        if cookie:
            self.session_cookie = cookie
        return True

    def _logout(self):
        self.ensure_one()
        if self.auth_type == "session" and self.session_cookie:
            try:
                self._call("logout", {})
            except UserError:
                pass
            self.session_cookie = False
        return True

    def test_connection(self):
        """Validate credentials with a lightweight read."""
        self.ensure_one()
        if self.auth_type == "session" and not self.session_cookie:
            self._login()
        self._call("inventory/by-customer", {"custid": self.custid, "facility": self.facility})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "sticky": False,
                "message": _("Synapse connection is OK!"),
            },
        }

    def _notify(self, message, kind="success"):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"type": kind, "sticky": False, "message": message},
        }

    def action_enqueue_inventory(self):
        self.ensure_one()
        count = self.enqueue_inventory()
        return self._notify(_("%s inventory job(s) added to the queue.") % count)

    def action_enqueue_shipments(self):
        self.ensure_one()
        count = self.enqueue_shipments()
        return self._notify(_("%s shipment job(s) added to the queue.") % count)

    def action_process_queue(self):
        """Drain the queue now (handy button next to the sync actions)."""
        self.ensure_one()
        self.env["synapse.queue.job"].cron_process_queue()
        return self._notify(_("Queue processed."))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @api.model
    def _get_default_connection(self):
        connection = self.search([("active", "=", True)], limit=1)
        if not connection:
            raise UserError(_("No active Synapse connection is configured."))
        return connection

    @staticmethod
    def _truncate_fields(values, maxlen):
        """Truncate each string in ``values`` to the limit in ``maxlen``.

        Non-string values (numbers/booleans) and fields without a declared
        limit are left untouched. Returns the same dict for convenience.
        """
        for key, limit in maxlen.items():
            val = values.get(key)
            if isinstance(val, str) and len(val) > limit:
                values[key] = val[:limit]
        return values

    @staticmethod
    def _to_synapse_date(value, with_time=False):
        if not value:
            return False
        if isinstance(value, str):
            value = fields.Datetime.to_datetime(value)
        return value.strftime(DATETIME_FMT if with_time else DATE_FMT)

    # ------------------------------------------------------------------
    # Orders: Odoo -> Synapse
    # ------------------------------------------------------------------
    def _order_shipping_service(self, order, override=None, strict=False):
        """Resolve the shipping service/carrier for an order.

        Priority: explicit override -> the Odoo delivery carrier on the order
        (when the delivery module is installed) -> the connection default.

        ``delivery_service`` is a 4-char *code* field in Synapse, so a real
        ``synapse_service_code`` mapped on the delivery method is always
        preferred. When ``strict`` is True the raw carrier *name* fallback is
        suppressed (it would be truncated to a meaningless 4-char string that
        Synapse rejects with "Invalid delivery service code", API-00069) so the
        caller can validate the configuration before sending.
        """
        if override:
            return override
        carrier = getattr(order, "carrier_id", False)
        if carrier:
            if carrier.synapse_service_code:
                return carrier.synapse_service_code
            # No mapped code: in strict mode refuse the truncated-name fallback.
            return "" if strict else carrier.name
        return self.default_shipping_service or ""

    def _order_carrier_code(self, order, override=None):
        """Resolve the Synapse carrier code (transport company) for an order.

        Distinct from the service level: maps to the create-order ``carrier``
        header field (max 10). Priority: explicit override -> the shipping
        method's ``synapse_carrier_code`` -> auto-detected from the method's
        ``delivery_type``/name. Returns "" when nothing matches so no carrier is
        sent (the field is optional)."""
        if override:
            return override
        carrier = getattr(order, "carrier_id", False)
        if not carrier:
            return ""
        if carrier.synapse_carrier_code:
            return carrier.synapse_carrier_code
        return self._guess_carrier_code(carrier)

    @staticmethod
    def _guess_carrier_code(carrier):
        """Best-effort carrier code from a shipping method with no explicit
        ``synapse_carrier_code``: use the structured ``delivery_type`` for real
        integrations, else scan the method name for a known carrier keyword."""
        dtype = (carrier.delivery_type or "").lower()
        if dtype in SYNAPSE_CARRIER_BY_DELIVERY_TYPE:
            return SYNAPSE_CARRIER_BY_DELIVERY_TYPE[dtype]
        name = (carrier.name or "").lower()
        for keyword, code in SYNAPSE_CARRIER_NAME_KEYWORDS:
            if keyword in name:
                return code
        return ""

    @staticmethod
    def _is_pickable_line(line):
        """Return True when an order line is a physical good to pick/ship.

        Excludes the lines that must NOT be sent to the warehouse as items:
        section/note lines (``display_type``), the delivery module's shipping
        charge line (``is_delivery``), and service products (freight, handling,
        fees) — sending those as pick lines makes Synapse try to pick a SKU that
        isn't real inventory.
        """
        if not line.product_id or line.display_type:
            return False
        if getattr(line, "is_delivery", False):
            return False
        if line.product_id.type == "service":
            return False
        return True

    @staticmethod
    def _is_receivable_line(line):
        """Return True when a purchase order line is a physical good to receive.

        The inbound mirror of ``_is_pickable_line``: excludes section/note
        lines (``display_type``) and service products. Purchase lines have no
        ``is_delivery`` shipping-charge line."""
        if not line.product_id or line.display_type:
            return False
        if line.product_id.type == "service":
            return False
        return True

    def build_order_payload(
            self, order, order_type=None, line_values=None, shipping_service=None,
            carrier_code=None, priority=None,
    ):
        """Build the create-order request body from a sale.order.

        :param line_values: optional list of dicts ``{product, qty, uom}`` to
            override the order lines (used by the wizard).
        :param shipping_service: explicit shipping service / carrier; falls back
            to the order's delivery carrier then the connection default.
        """
        self.ensure_one()
        partner = order.partner_shipping_id or order.partner_id
        header = {
            "custid": self.custid,
            "order_type": order_type or self.default_order_type or "",
            "po_number": order.client_order_ref or order.name,
            "reference": order.name,
            "ship_to_name": partner.name or "",
            "ship_to_contact": partner.name or "",
            "ship_to_address_1": partner.street or "",
            "ship_to_address_2": partner.street2 or "",
            "ship_to_city": partner.city or "",
            "ship_to_state": partner.state_id.code or "",
            "ship_to_postal_code": partner.zip or "",
            "ship_to_country_code": partner.country_id.code or "",
            "ship_to_phone": partner.phone or "",
            "ship_to_email": partner.email or "",
        }
        # Outbound order types (O/V/F/T/U) require from_facility. Fall back to
        # the connection's main facility so a sales order never fails with
        # "from_facility is required". to_facility stays explicit (only inbound
        # / transfer orders need it; a sales order ships out, not into another
        # facility).
        from_facility = self.from_facility or self.facility
        if from_facility:
            header["from_facility"] = from_facility
        if self.to_facility:
            header["to_facility"] = self.to_facility
        service = self._order_shipping_service(order, override=shipping_service)
        if service:
            header[self.shipping_service_key or "delivery_service"] = service
        # carrier (transport company) is a separate header field from the
        # service level; send it when the carrier has a mapped Synapse code.
        carrier = self._order_carrier_code(order, override=carrier_code)
        if carrier:
            header["carrier"] = carrier
        # Order priority (1-char code, e.g. "0"=Hot, "A"=Normal). Sent when set
        # on the call or configured as the connection default.
        prio = priority or self.default_priority
        if prio:
            header["priority"] = prio

        # Synapse rejects optional header fields sent as empty strings with
        # "API-00097 '<field>' is not allowed to be empty". Drop every blank so
        # only populated keys are sent. The required keys (custid, order_type,
        # po_number, reference) are always populated above, so this never
        # strips a mandatory field — it just stops the empty-field whack-a-mole.
        header = {k: v for k, v in header.items() if v not in (None, False, "")}
        # Synapse also enforces a maxLength per field ("API-00097 '<field>'
        # length must be less than or equal to N"). Truncate every string to
        # its documented limit so an over-long address/name/etc. can't fail.
        header = self._truncate_fields(header, SYNAPSE_HEADER_MAXLEN)

        if line_values is None:
            line_values = [
                {
                    "product": line.product_id,
                    "qty": line.product_uom_qty,
                    "uom": False,
                }
                for line in order.order_line
                if self._is_pickable_line(line)
            ]

        details = self._build_synapse_details(order, line_values)

        # Pre-flight: refuse the order if any line's SKU is unknown to Synapse,
        # or if the demanded quantity exceeds Synapse's on-hand — so the user
        # gets one clear message instead of a create-order rejection.
        self._validate_order_items(details)

        return {"header": header, "details": details}

    def _build_synapse_details(self, order, line_values):
        """Build the create-order ``details`` rows shared by the outbound and
        inbound payload builders.

        Strict SKU (Internal Reference only — Synapse matches lines to its
        inventory by the ``item`` code), positive whole-number quantities, and
        the default UoM. Does NOT run the outbound on-hand pre-flight: callers
        decide (it is meaningless for an inbound receipt — see
        ``build_inbound_order_payload``)."""
        self.ensure_one()
        details = []
        for lv in line_values:
            product = lv["product"]

            # SKU = the product's Internal Reference ONLY. Synapse matches order
            # lines to its inventory by the `item` code, which equals the Odoo
            # Internal Reference (default_code). The old Product Name / Barcode
            # fallbacks sent SKUs Synapse doesn't know, so those lines never
            # mapped — removed. No Internal Reference => refuse the order.
            sku = product.default_code
            if not sku:
                raise UserError(
                    _("Product '%(name)s' does not exist on Synapse Anywhere.")
                    % {"name": product.display_name}
                )

            # Synapse's create-order qty_entered is an integer (int64), so we
            # must NOT silently truncate (int(2.5) -> 2 ships the wrong amount)
            # nor send a float (rejected by the API). Refuse non-positive and
            # fractional quantities up front with a clear message instead.
            qty = lv.get("qty") or 0
            if qty <= 0:
                raise UserError(
                    _(
                        "Order %(order)s: line for '%(item)s' has a non-positive "
                        "quantity (%(qty)s); cannot send to Synapse."
                    ) % {"order": order.name, "item": sku, "qty": qty}
                )
            if not float(qty).is_integer():
                raise UserError(
                    _(
                        "Order %(order)s: line for '%(item)s' has a fractional "
                        "quantity (%(qty)s). Synapse only accepts whole-number "
                        "quantities, so this order cannot be sent as-is. Adjust "
                        "the quantity (or the unit of measure) to a whole number."
                    ) % {"order": order.name, "item": sku, "qty": qty}
                )
            qty_entered = int(qty)

            details.append(
                self._truncate_fields(
                    {
                        "item": sku,
                        "uom_entered": lv.get("uom") or self.default_uom or "EA",
                        "qty_entered": qty_entered,
                    },
                    SYNAPSE_DETAIL_MAXLEN,
                )
            )

        if not details:
            raise UserError(_("Order %s has no lines to send to Synapse.") % order.name)
        return details

    def build_inbound_order_payload(self, order, order_type=None, line_values=None,
                                    priority=None):
        """Build the create-order request body from a purchase.order (inbound).

        The warehouse-import mirror of ``build_order_payload``: the deliver-to
        (ship_to_*) is the connection's Inbound Warehouse address (where the
        goods must arrive), the vendor rides the ``shipper_*`` block as the
        origin, and the order receives INTO ``to_facility`` (inbound types
        require it — no from_facility, no carrier / shipping service: an
        import is not an outbound shipment).

        :param order: a ``purchase.order`` record
        :param order_type: optional inbound order_type override; defaults to
            the connection's Default Inbound Order Type
        :param line_values: optional list of dicts ``{product, qty, uom}`` to
            override the order lines (used by the purchase-order wizard)
        :param priority: optional priority override
        """
        self.ensure_one()
        warehouse_partner = self.inbound_warehouse_id.partner_id
        if not warehouse_partner:
            raise UserError(
                _(
                    "Synapse connection '%(conn)s' has no Inbound Warehouse set. "
                    "Purchase orders are sent with the receiving warehouse's "
                    "address as the deliver-to (e.g. 'Baghouse America - AZ' / "
                    "its Receipts dock) — set it on the connection first."
                ) % {"conn": self.name}
            )
        vendor = order.partner_id
        header = {
            "custid": self.custid,
            "order_type": order_type or self.default_inbound_order_type or "R",
            "po_number": order.partner_ref or order.name,
            "reference": order.name,
            # Deliver-to: the receiving warehouse — goods ARRIVE here.
            "ship_to_name": warehouse_partner.name or "",
            "ship_to_contact": warehouse_partner.name or "",
            "ship_to_address_1": warehouse_partner.street or "",
            "ship_to_address_2": warehouse_partner.street2 or "",
            "ship_to_city": warehouse_partner.city or "",
            "ship_to_state": warehouse_partner.state_id.code or "",
            "ship_to_postal_code": warehouse_partner.zip or "",
            "ship_to_country_code": warehouse_partner.country_id.code or "",
            "ship_to_phone": warehouse_partner.phone or "",
            "ship_to_email": warehouse_partner.email or "",
            # Origin: the vendor the goods are imported from.
            "shipper_name": vendor.name or "",
            "shipper_contact": vendor.name or "",
            "shipper_address_1": vendor.street or "",
            "shipper_address_2": vendor.street2 or "",
            "shipper_city": vendor.city or "",
            "shipper_state": vendor.state_id.code or "",
            "shipper_postal_code": vendor.zip or "",
            "shipper_country_code": vendor.country_id.code or "",
            "shipper_phone": vendor.phone or "",
            "shipper_email": vendor.email or "",
        }
        # Inbound types (A/C/I/P/Q/R) require to_facility — goods are received
        # INTO it. Mirror of the outbound from_facility fallback: the dedicated
        # inbound code wins, then the connection's general To Facility, then
        # its main Facility. No from_facility is ever sent.
        to_facility = self.inbound_to_facility or self.to_facility or self.facility
        if to_facility:
            header["to_facility"] = to_facility
        # Order priority: explicit override, else the connection default.
        prio = priority or self.default_priority
        if prio:
            header["priority"] = prio

        # Same blank-drop + truncate pipeline as the outbound header (an empty
        # optional field is rejected with API-00097; an over-long one too).
        header = {k: v for k, v in header.items() if v not in (None, False, "")}
        header = self._truncate_fields(header, SYNAPSE_HEADER_MAXLEN)

        if line_values is None:
            line_values = [
                {
                    "product": line.product_id,
                    "qty": line.product_qty,
                    "uom": False,
                }
                for line in order.order_line
                if self._is_receivable_line(line)
            ]
        # No _validate_order_items here: that pre-flight checks demand against
        # Synapse ON-HAND, which is meaningless for a receipt — imported items
        # are typically NOT yet in the inventory snapshot (that false-negative
        # would block every new import). Synapse itself rejects unknown items
        # on create-order with a clear API error.
        details = self._build_synapse_details(order, line_values)
        return {"header": header, "details": details}

    def _synapse_available_map(self):
        """Return ``{synapse_item_code: on_hand_qty}`` from this connection's
        stored inventory snapshot, summed across lots/UoMs.

        Used at sale-order confirmation to decide whether Synapse (Freeport) can
        fulfil a drop-ship line from stock (create a Delivery Order) or whether
        an RFQ/PO must be raised instead. Reads the last synced snapshot rather
        than calling the API so confirmation stays fast and offline — the same
        basis ``_validate_order_items`` already trusts.
        """
        self.ensure_one()
        available = {}
        snapshot = self.env["synapse.inventory.line"].search(
            [("connection_id", "=", self.id)]
        )
        for line in snapshot:
            if line.item:
                available[line.item] = available.get(line.item, 0.0) + (
                    line.qty or 0.0
                )
        return available

    def _validate_order_items(self, details):
        """Validate order lines against the latest Synapse inventory snapshot.

        Two checks, both raising a clear UserError:
          1. every SKU must be a known Synapse item, and
          2. the demanded quantity must not exceed Synapse's on-hand quantity.

        Validates against the stored inventory snapshot (synapse.inventory.line)
        rather than a live call: it is offline/fast and any item being shipped
        will be in inventory. Skipped when no snapshot exists yet (nothing to
        validate against — let Synapse's create-order decide in that case).

        NOTE: quantities are compared in the snapshot's own UoM; this assumes the
        order line UoM matches Synapse's (true for BHA's 'Each' items)."""
        self.ensure_one()
        Line = self.env["synapse.inventory.line"]
        snapshot = Line.search([("connection_id", "=", self.id)])
        if not snapshot:
            _logger.info(
                "Synapse: no inventory snapshot for %s; skipping item validation.",
                self.name,
            )
            return
        # On-hand per item, summed across lots/UoMs in the snapshot.
        available = {}
        for line in snapshot:
            if line.item:
                available[line.item] = available.get(line.item, 0.0) + (line.qty or 0.0)
        # Demand per item, aggregated across this order's lines.
        demand = {}
        for d in details:
            item = d.get("item")
            if item:
                demand[item] = demand.get(item, 0) + (d.get("qty_entered") or 0)

        # 1) Unknown items.
        missing = sorted(s for s in demand if s not in available)
        if missing:
            raise UserError(
                _(
                    "These products do not exist on Synapse Anywhere: %s.\n\n"
                    "(Checked against the last inventory sync. If an item was "
                    "just added in Synapse, pull inventory again and retry.)"
                ) % ", ".join(missing)
            )

        # 2) Insufficient on-hand.
        short = sorted(
            (s, demand[s], available[s])
            for s in demand
            if demand[s] > available[s]
        )
        if short:
            rows = "\n".join(
                _("  • %(item)s: ordered %(d)s, available %(a)s")
                % {"item": s, "d": "%g" % d, "a": "%g" % a}
                for s, d, a in short
            )
            raise UserError(
                _(
                    "These products do not have enough quantity on Synapse "
                    "Anywhere:\n%s\n\n"
                    "(Checked against the last inventory sync. Pull inventory "
                    "again if stock has since changed.)"
                ) % rows
            )

    def push_order(self, order, order_type=None, line_values=None, shipping_service=None,
                   carrier_code=None, priority=None):
        """Create an order in Synapse and store the returned ids on the SO."""
        self.ensure_one()
        # Idempotency guard: never create a second Synapse order for one that is
        # already sent. This catches duplicate queue jobs and a re-run of a job
        # whose create-order succeeded but whose state write was rolled back.
        # (Resync intentionally clears synapse_orderid first, so it is unaffected.)
        if order.synapse_orderid:
            _logger.info(
                "Synapse: order %s already has order id %s; skipping create.",
                order.name, order.synapse_orderid,
            )
            return {
                "orderid": order.synapse_orderid,
                "shipid": order.synapse_shipid,
                "wave": order.synapse_wave,
            }
        payload = self.build_order_payload(
            order,
            order_type=order_type,
            line_values=line_values,
            shipping_service=shipping_service,
            carrier_code=carrier_code,
            priority=priority,
        )
        result = self._call("orders/create-order", payload)
        order.write(
            {
                "synapse_connection_id": self.id,
                "synapse_orderid": result.get("orderid"),
                "synapse_shipid": result.get("shipid"),
                "synapse_wave": result.get("wave"),
                "synapse_status": "sent",
                # Clear the push flag so cron_push_orders won't re-enqueue it.
                "synapse_to_send": False,
            }
        )
        # Persist the returned ids immediately. If anything after this throws,
        # the queue would roll back and retry — but the ids are now committed, so
        # the idempotency guard above prevents a duplicate create-order.
        self.env.cr.commit()
        order.message_post(
            body=_("Sent to Synapse. Order ID: %s, Ship ID: %s")
            % (result.get("orderid"), result.get("shipid"))
        )
        if self.auto_send_packing_slip and not order.synapse_customer_packing_list:
            self._auto_send_packing_slip(order)
        return result

    def enqueue_order(self, order, order_type=None, line_values=None, shipping_service=None,
                      carrier_code=None, priority=None):
        """Enqueue an order export job. ``line_values`` is the wizard's
        ``[{product, qty, uom}]`` form; it is serialised into the job payload."""
        self.ensure_one()
        payload = {
            "order_type": order_type,
            "shipping_service": shipping_service,
            "carrier_code": carrier_code,
            "priority": priority,
        }
        if line_values:
            payload["lines"] = [
                {
                    "product_id": lv["product"].id,
                    "qty": lv.get("qty"),
                    "uom": lv.get("uom"),
                }
                for lv in line_values
            ]
        return self.env["synapse.queue.job"]._enqueue(
            self,
            "order",
            "export",
            name=_("Order %s") % order.name,
            payload=payload,
            res_model="sale.order",
            res_id=order.id,
            synapse_ref=order.name,
        )

    def push_inbound_order(self, order, order_type=None, line_values=None,
                           priority=None):
        """Create an inbound order in Synapse from a purchase.order and store
        the returned ids on the PO. The inbound mirror of ``push_order``.

        :param order: a ``purchase.order`` record
        :param line_values: optional ``[{product, qty, uom}]`` wizard overrides
        """
        self.ensure_one()
        # Same idempotency guard as push_order: never create a second Synapse
        # order for one already sent. This catches duplicate queue jobs and a
        # re-run of a job whose create-order succeeded but whose state write
        # was rolled back. (Resync clears synapse_orderid first, so it is
        # unaffected.)
        if order.synapse_orderid:
            _logger.info(
                "Synapse: purchase order %s already has order id %s; skipping create.",
                order.name, order.synapse_orderid,
            )
            return {
                "orderid": order.synapse_orderid,
                "shipid": order.synapse_shipid,
                "wave": order.synapse_wave,
            }
        payload = self.build_inbound_order_payload(
            order,
            order_type=order_type,
            line_values=line_values,
            priority=priority,
        )
        result = self._call("orders/create-order", payload)
        order.write(
            {
                "synapse_connection_id": self.id,
                "synapse_orderid": result.get("orderid"),
                "synapse_shipid": result.get("shipid"),
                "synapse_wave": result.get("wave"),
                "synapse_status": "sent",
                "synapse_to_send": False,
            }
        )
        # Persist the returned ids immediately (same rationale as push_order:
        # a later failure + queue retry must not create a duplicate).
        self.env.cr.commit()
        order.message_post(
            body=_("Sent to Synapse as an inbound order. Order ID: %s, Ship ID: %s")
            % (result.get("orderid"), result.get("shipid"))
        )
        return result

    def enqueue_inbound_order(self, order, order_type=None, line_values=None,
                              priority=None):
        """Enqueue a purchase-order (inbound) export job. ``line_values`` is
        the wizard's ``[{product, qty, uom}]`` form; it is serialised into the
        job payload like the sale-order path."""
        self.ensure_one()
        payload = {
            "order_type": order_type,
            "priority": priority,
        }
        if line_values:
            payload["lines"] = [
                {
                    "product_id": lv["product"].id,
                    "qty": lv.get("qty"),
                    "uom": lv.get("uom"),
                }
                for lv in line_values
            ]
        return self.env["synapse.queue.job"]._enqueue(
            self,
            "purchase_order",
            "export",
            name=_("Purchase Order %s") % order.name,
            payload=payload,
            res_model="purchase.order",
            res_id=order.id,
            synapse_ref=order.name,
        )

    def cancel_order(self, order):
        """Cancel a previously created order in Synapse.

        ``mhe/cancel-order`` is a bulk endpoint that returns HTTP 200 even when
        it cancels nothing (e.g. a Hold/uncommitted order with no wave/MHE work
        to cancel), so a 200 alone does NOT mean the order is cancelled. We
        confirm against order-info that the status actually moved to ``X``
        (Cancelled) and only then mark the SO cancelled — otherwise we raise so
        the user isn't told "cancelled" while Synapse still shows Hold."""
        self.ensure_one()
        if not order.synapse_orderid:
            raise UserError(_("Order %s has no Synapse order id.") % order.name)
        # cancel-order takes an `orders` array (NOT a flat {orderid, shipid} —
        # that fails with "'orders' is required", API-00153). Each item has a
        # single string `orderId` that combines the order id and ship id as
        # "<orderid>-<shipid>" (per the API schema, e.g. "86753-09").
        ship = order.synapse_shipid or "1"
        result = self._call(
            "mhe/cancel-order",
            {"orders": [{"orderId": "%s-%s" % (order.synapse_orderid, ship)}]},
        )
        # Verify the cancel actually took effect rather than trusting the 200.
        status = None
        try:
            info = self.get_order_info(order.synapse_orderid, order.synapse_shipid)
            rows = info.get("order") if isinstance(info, dict) else None
            if rows:
                status = str(rows[0].get("order_status"))
        except UserError as exc:
            _logger.warning(
                "Synapse: could not confirm cancel of %s: %s", order.name, exc
            )
        if status == "X":
            order.write(
                {"synapse_status": "cancelled", "synapse_order_status": "X"}
            )
            return result
        # Not cancelled — record the real status and tell the truth.
        if status:
            order.write({"synapse_order_status": status})
        label = dict(SYNAPSE_ORDER_STATUSES).get(status, status or _("unknown"))
        raise UserError(
            _(
                "Synapse accepted the cancel request for %(order)s but the order "
                "is NOT cancelled — its Synapse status is still '%(status)s'. "
                "An order is often only cancellable once it has been released "
                "from Hold/committed; a Hold order may have nothing for the "
                "cancel endpoint to act on. Check the order directly in Synapse."
            ) % {"order": order.name, "status": label}
        )

    def build_order_update_payload(
            self, order, shipping_service=None, carrier_code=None, extra=None,
    ):
        """Build the orders/update-order body for an already-sent order.

        IMPORTANT: Synapse's update-order is *header only* — it updates
        carrier / shipping-service / routing fields, but it has no details
        array, so it CANNOT change line items, quantities, or the ship-to
        address. Use :meth:`resync_order` for those.

        :param extra: optional dict of raw update-order fields (e.g.
            ``{"shippingcost": 12.5, "prono": "1Z..."}``) merged in last.
        """
        self.ensure_one()
        if not order.synapse_orderid:
            raise UserError(
                _("Order %s has no Synapse order id; send it to Synapse first.")
                % order.name
            )
        # Build the optional fields first; the empty-value filter below must not
        # touch orderid/shipid (0 == False in Python, so a 0 would be dropped).
        optional = {}
        # strict=True: never push a truncated carrier *name* as the service
        # code — Synapse rejects unknown codes with API-00069. If the order has
        # a delivery method but no mapped Synapse Service Code, tell the user
        # exactly what to set instead of letting the API fail cryptically.
        service = self._order_shipping_service(
            order, override=shipping_service, strict=True
        )
        carrier_on_order = getattr(order, "carrier_id", False)
        if not service and carrier_on_order and not shipping_service:
            raise UserError(
                _(
                    "Cannot update order %(order)s in Synapse: the delivery "
                    "method '%(method)s' has no Synapse Service Code, so "
                    "Synapse would reject it with \"Invalid delivery service "
                    "code\".\n\n"
                    "Set the 'Synapse Service Code' field on that delivery "
                    "method (Inventory → Configuration → Delivery Methods → "
                    "'%(method)s', Synapse tab) to a valid 4-character Synapse "
                    "code, then update again."
                ) % {"order": order.name, "method": carrier_on_order.name}
            )
        if service:
            optional["deliveryservice"] = service
        carrier = self._order_carrier_code(order, override=carrier_code)
        if carrier:
            optional["carrier"] = carrier
        if extra:
            for key, val in extra.items():
                if val not in (None, False, ""):
                    optional[key] = val
        optional = self._truncate_fields(optional, SYNAPSE_UPDATE_MAXLEN)
        # update-order only requires orderid/shipid, but an update with no
        # changed field is a useless (and possibly rejected) call. With no
        # delivery carrier on the order and no connection default service,
        # ``optional`` is empty — refuse rather than send a no-op.
        if not optional:
            raise UserError(
                _(
                    "Order %s has no shipping/carrier information to update in "
                    "Synapse. Set a Delivery Method on the order (or a Default "
                    "Shipping Service on the connection), or pass explicit "
                    "update fields, before updating."
                ) % order.name
            )
        payload = {
            "orderid": int(order.synapse_orderid),
            "shipid": int(order.synapse_shipid or 1),
        }
        payload.update(optional)
        return payload

    def update_order(self, order, shipping_service=None, carrier_code=None, extra=None):
        """Push header-level shipping/routing changes to Synapse (update-order).

        Only carrier / shipping-service / routing fields can change this way;
        line/quantity/address changes need :meth:`resync_order`."""
        self.ensure_one()
        payload = self.build_order_update_payload(
            order, shipping_service=shipping_service, carrier_code=carrier_code,
            extra=extra,
        )
        result = self._call("orders/update-order", payload)
        order.message_post(
            body=_("Order updated in Synapse (order id %s).") % order.synapse_orderid
        )
        return result

    def enqueue_update_order(self, order, shipping_service=None, carrier_code=None,
                             extra=None):
        """Enqueue a header-level update-order export job."""
        self.ensure_one()
        payload = {
            "shipping_service": shipping_service,
            "carrier_code": carrier_code,
            "extra": extra or {},
        }
        return self.env["synapse.queue.job"]._enqueue(
            self,
            "update",
            "export",
            name=_("Update %s") % order.name,
            payload=payload,
            res_model="sale.order",
            res_id=order.id,
            synapse_ref=order.synapse_orderid,
        )

    def resync_order(self, order, order_type=None, line_values=None,
                     shipping_service=None, carrier_code=None, priority=None):
        """Replace an order in Synapse when its lines/quantities/address change.

        Synapse's update-order cannot change order content, so the only way to
        push such changes is to cancel the existing Synapse order and create a
        fresh one. The new orderid/shipid are written back onto the SO by
        :meth:`push_order`. A failed cancel (e.g. already shipped) is logged but
        does not stop the recreate."""
        self.ensure_one()
        if order.synapse_orderid:
            try:
                self.cancel_order(order)
            except UserError as exc:
                _logger.warning(
                    "Synapse resync: cancel of %s failed (%s); recreating anyway.",
                    order.name, exc,
                )
        order.write({
            "synapse_orderid": False,
            "synapse_shipid": False,
            "synapse_wave": False,
            "synapse_order_status": False,
            "synapse_status": "draft",
        })
        return self.push_order(
            order,
            order_type=order_type,
            line_values=line_values,
            shipping_service=shipping_service,
            carrier_code=carrier_code,
            priority=priority,
        )

    def resync_inbound_order(self, order, order_type=None):
        """Replace a purchase order in Synapse when its lines/quantities
        change: cancel the existing inbound order and create a fresh one (the
        inbound mirror of ``resync_order`` — update-order cannot change order
        content). A failed cancel (e.g. already received) is logged but does
        not stop the recreate.

        :param order: a ``purchase.order`` record"""
        self.ensure_one()
        if order.synapse_orderid:
            try:
                self.cancel_order(order)
            except UserError as exc:
                _logger.warning(
                    "Synapse resync: cancel of purchase order %s failed (%s); "
                    "recreating anyway.", order.name, exc,
                )
        order.write({
            "synapse_orderid": False,
            "synapse_shipid": False,
            "synapse_wave": False,
            "synapse_order_status": False,
            "synapse_status": "draft",
        })
        return self.push_inbound_order(order, order_type=order_type)

    def get_order_info(self, orderid, shipid=None):
        self.ensure_one()
        # Synapse's order-info expects orderid/shipid as *numbers*. The values
        # come from the Char fields synapse_orderid/synapse_shipid, so coerce
        # them to int — sending them as strings is rejected with
        # "Expected number, received string at orderid/shipid" (API-00087).
        payload = {"orderid": self._coerce_synapse_id(orderid, "orderid")}
        if shipid:
            payload["shipid"] = self._coerce_synapse_id(shipid, "shipid")
        return self._call("orders/order-info", payload)

    @staticmethod
    def _coerce_synapse_id(value, field):
        """Coerce a Synapse order/ship id to the int the API requires."""
        try:
            return int(value)
        except (TypeError, ValueError):
            raise UserError(
                _("Invalid Synapse %(field)s %(value)r — it must be a number.")
                % {"field": field, "value": value}
            )

    def refresh_order_status(self, order):
        """Pull the order's current Synapse status (order-info) onto the order.

        Works for both a sale.order (outbound) and a purchase.order (inbound).
        Stores the granular Synapse code in ``synapse_order_status`` and syncs
        the high-level ``synapse_status`` for the milestones: Shipped (9,
        outbound only — it stamps synapse_shipped_date, which only the SO
        has), Cancelled (X), and — for inbound orders — Arrived/Received
        (A/R → 'received', stamping synapse_received_date once). Returns the
        order-info row, or ``False`` if not found."""
        self.ensure_one()
        if not order.synapse_orderid:
            raise UserError(_("Order %s has no Synapse order id.") % order.name)
        info = self.get_order_info(order.synapse_orderid, order.synapse_shipid)
        rows = info.get("order") if isinstance(info, dict) else None
        if not rows:
            return False
        row = rows[0]
        code = row.get("order_status")
        inbound = order._name == "purchase.order"
        vals = {}
        if code is not None:
            vals["synapse_order_status"] = str(code)
            if str(code) == "9" and not inbound:  # Shipped (outbound only)
                vals["synapse_status"] = "shipped"
                # Stamp once, same as the shipment-import path — so an order
                # first marked "Shipped" via this status-refresh (rather than
                # a pulled shipment) still shows up in the daily report.
                if not order.synapse_shipped_date:
                    vals["synapse_shipped_date"] = fields.Date.context_today(order)
            elif str(code) == "X":  # Cancelled
                vals["synapse_status"] = "cancelled"
            elif inbound and str(code) in ("A", "R"):
                # Arrived / Received: the inbound terminal statuses. Note
                # order_status 'R' (Received) is a different thing from the
                # order_type 'R' (Receipt) — this keys off the status code.
                vals["synapse_status"] = "received"
                if not order.synapse_received_date:
                    vals["synapse_received_date"] = fields.Date.context_today(order)
        if vals:
            order.write(vals)
        if self.auto_validate_delivery and str(code) == "9" and not inbound:
            if order.synapse_shipment_ids:
                for shipment in order.synapse_shipment_ids:
                    shipment._auto_validate_order_delivery(order)
            else:
                try:
                    self.pull_shipments()
                except Exception as exc:
                    _logger.warning("Could not pull shipments for order %s: %s", order.name, exc)
        return row

    @api.model
    def cron_push_orders(self):
        """Cron: enqueue confirmed sale orders flagged for Synapse that are not
        yet sent (the queue processor then pushes them)."""
        orders = self.env["sale.order"].search(
            [
                ("synapse_to_send", "=", True),
                ("synapse_status", "in", [False, "draft"]),
                ("state", "in", ["sale", "done"]),
            ]
        )
        default = self.search([("active", "=", True)], limit=1)
        Job = self.env["synapse.queue.job"]
        for order in orders:
            connection = order.synapse_connection_id or default
            if not connection:
                continue
            # Skip orders that already have an open order-export job (e.g. one
            # the wizard just enqueued): without this, the cron would enqueue a
            # second push and create a duplicate Synapse order.
            pending = Job.search_count(
                [
                    ("res_model", "=", "sale.order"),
                    ("res_id", "=", order.id),
                    ("job_type", "=", "order"),
                    ("direction", "=", "export"),
                    ("state", "in", ["new", "progress"]),
                ]
            )
            if pending:
                continue
            try:
                connection.enqueue_order(order)
            except UserError as exc:
                _logger.error("Synapse enqueue_order failed for %s: %s", order.name, exc)
                continue

    @api.model
    def cron_retry_insufficient_inventory_orders(self):
        """Automated 45-minute check for past orders that were not originally
        processed due to insufficient inventory."""
        for connection in self.search([("active", "=", True)]):
            try:
                connection._retry_insufficient_inventory_orders()
            except Exception as exc:
                _logger.exception(
                    "Error retrying insufficient inventory orders for connection %s: %s",
                    connection.name,
                    exc,
                )

    def _retry_insufficient_inventory_orders(self):
        """Find orders flagged for Synapse that were not sent due to insufficient
        inventory, refresh their inventory status, and retry sending them."""
        self.ensure_one()
        orders = self.env["sale.order"].search([
            ("synapse_connection_id", "in", [self.id, False]),
            ("synapse_to_send", "=", True),
            ("synapse_orderid", "=", False),
            ("state", "in", ["sale", "done"]),
        ])
        if not orders:
            return

        Job = self.env["synapse.queue.job"]
        failed_jobs = Job.search([
            ("res_model", "=", "sale.order"),
            ("res_id", "in", orders.ids),
            ("job_type", "in", ["order", "resync"]),
            ("state", "=", "failed"),
        ])
        insufficient_jobs = failed_jobs.filtered(
            lambda j: not j.message or any(
                term in j.message.lower()
                for term in ("quantity", "inventory", "available", "stock", "enough")
            )
        )
        orders_from_jobs = insufficient_jobs.mapped(
            lambda j: j._linked_sale_order()
        ).filtered(lambda o: o and not o.synapse_orderid)

        waiting_orders = orders.filtered(
            lambda o: o.id not in failed_jobs.mapped("res_id")
            and any(
                p.picking_type_code == "outgoing"
                and p.state in ("confirmed", "waiting")
                for p in o.picking_ids
            )
        )
        target_orders = (orders_from_jobs | waiting_orders).filtered(
            lambda o: not o.synapse_orderid
        )
        if not target_orders:
            return

        # Pull latest inventory for the required SKUs from Synapse
        items_to_check = set()
        for order in target_orders:
            for line in order.order_line:
                if self._is_pickable_line(line) and line.product_id.default_code:
                    items_to_check.add(line.product_id.default_code)

        for item in items_to_check:
            try:
                self.pull_inventory(item=item)
            except Exception as exc:
                _logger.warning(
                    "Could not pull inventory for item %s on connection %s: %s",
                    item,
                    self.name,
                    exc,
                )

        # Trigger availability check on outgoing pickings in Odoo
        for order in target_orders:
            pickings = order.picking_ids.filtered(
                lambda p: p.picking_type_code == "outgoing"
                and p.state not in ("done", "cancel")
            )
            for picking in pickings:
                try:
                    picking.action_assign()
                except Exception as exc:
                    _logger.warning(
                        "action_assign failed for picking %s: %s",
                        picking.name,
                        exc,
                    )

        # Retry sending the orders
        for order in target_orders:
            order_jobs = insufficient_jobs.filtered(lambda j: j.res_id == order.id)
            if order_jobs:
                for job in order_jobs:
                    job.write({"state": "new"})
                    job.process()
            else:
                try:
                    job = self.enqueue_order(order)
                    job.process()
                except Exception as exc:
                    _logger.warning(
                        "Failed to retry order %s for Synapse: %s",
                        order.name,
                        exc,
                    )



    @api.model
    def cron_refresh_order_statuses(self):
        """Cron: refresh the granular Synapse status (order-info) of every
        order already sent but not yet shipped/cancelled — the same call the
        'Refresh Synapse Status' button makes, automated so statuses keep
        themselves current without anyone clicking per order. Never raises:
        one order's API failure must not stop the rest of the batch."""
        orders = self.env["sale.order"].search(
            [
                ("synapse_orderid", "!=", False),
                ("synapse_status", "=", "sent"),
                ("state", "in", ["sale", "done"]),
            ]
        )
        default = self.search([("active", "=", True)], limit=1)
        Job = self.env["synapse.queue.job"]
        done = failed = 0
        for order in orders:
            # Skip orders with an open export job (send / resync / cancel):
            # their Synapse state is about to be replaced, so a refresh now
            # would read a status that is obsolete the moment the job lands.
            pending = Job.search_count(
                [
                    ("res_model", "=", "sale.order"),
                    ("res_id", "=", order.id),
                    ("direction", "=", "export"),
                    ("job_type", "in", ["order", "resync", "cancel"]),
                    ("state", "in", ["new", "progress"]),
                ]
            )
            if pending:
                continue
            connection = order.synapse_connection_id or default
            if not connection:
                continue
            try:
                connection.refresh_order_status(order)
                done += 1
            except Exception:
                failed += 1
                _logger.exception(
                    "Synapse status refresh failed for order %s", order.name
                )
        if done or failed:
            _logger.info(
                "Synapse status cron: %s order(s) refreshed, %s failed",
                done,
                failed,
            )

    # ------------------------------------------------------------------
    # Inventory: Synapse -> Odoo
    # ------------------------------------------------------------------
    def _fetch_inventory_rows(self, item=None):
        """Call the Synapse inventory endpoint and return the raw rows."""
        self.ensure_one()
        if item:
            payload = {"custid": self.custid, "facility": self.facility, "item": item}
            data = self._call("inventory/by-item", payload)
        else:
            payload = {"custid": self.custid, "facility": self.facility}
            data = self._call("inventory/by-customer", payload)
        return data.get("inventory", []) if isinstance(data, dict) else []

    def enqueue_inventory(self, item=None):
        """Fetch inventory from Synapse and enqueue one import job per row."""
        self.ensure_one()
        Job = self.env["synapse.queue.job"]
        rows = self._fetch_inventory_rows(item=item)
        for row in rows:
            Job._enqueue(
                self,
                "inventory",
                "import",
                name=_("Inventory %s") % (row.get("item") or ""),
                payload=row,
                synapse_ref=row.get("item"),
            )
        self.last_inventory_sync = fields.Datetime.now()
        return len(rows)

    def pull_inventory(self, item=None):
        """Fetch on-hand inventory from Synapse and refresh the snapshot inline.

        Kept for direct/synchronous callers (e.g. the stock.quant button). The
        scheduled/queued path uses :meth:`enqueue_inventory` instead.
        """
        self.ensure_one()
        Line = self.env["synapse.inventory.line"]
        synced = Line
        for row in self._fetch_inventory_rows(item=item):
            synced |= Line._import_row(row, self)
        self.last_inventory_sync = fields.Datetime.now()
        return synced

    @api.model
    def cron_pull_inventory(self):
        for connection in self.search([("active", "=", True)]):
            try:
                connection.enqueue_inventory()
            except UserError as exc:
                _logger.error(
                    "Synapse enqueue_inventory failed for %s: %s", connection.name, exc
                )
                continue

    # ------------------------------------------------------------------
    # Shipments: Synapse -> Odoo
    # ------------------------------------------------------------------
    def _fetch_shipment_rows(self, begin_date=None, end_date=None):
        """Call the shipped-orders endpoint and return rows for this customer."""
        self.ensure_one()
        end_dt = fields.Datetime.to_datetime(end_date) if end_date else fields.Datetime.now()
        if begin_date:
            begin_dt = fields.Datetime.to_datetime(begin_date)
        elif self.last_shipment_sync:
            begin_dt = self.last_shipment_sync
        else:
            begin_dt = end_dt - timedelta(days=7)
        payload = {
            "request_type": "range",
            "begin_date": begin_dt.strftime(DATETIME_FMT),
            "end_date": end_dt.strftime(DATETIME_FMT),
            "facility": self.facility,
        }
        try:
            data = self._call("orders/shipped-orders", payload)
        except UserError as exc:
            # Synapse returns API-00080 ("No orders found within the provided
            # date range") as a 4xx. That is an empty result, not a failure, so
            # treat it as zero shipments instead of surfacing an error.
            if "API-00080" in str(exc) or "No orders found" in str(exc):
                _logger.info(
                    "Synapse: no shipments for %s in range %s..%s",
                    self.name, payload["begin_date"], payload["end_date"],
                )
                return [], end_dt
            raise
        orders = data.get("orders", []) if isinstance(data, dict) else []
        # The token is scoped to one customer; ignore stray rows.
        rows = [
            row
            for row in orders
            if not self.custid
            or (row.get("custid") or "").upper() == self.custid.upper()
        ]
        return rows, end_dt

    def enqueue_shipments(self, begin_date=None, end_date=None):
        """Fetch shipped orders and enqueue one import job per shipment."""
        self.ensure_one()
        Job = self.env["synapse.queue.job"]
        rows, end_dt = self._fetch_shipment_rows(begin_date, end_date)
        for row in rows:
            Job._enqueue(
                self,
                "shipment",
                "import",
                name=_("Shipment %s") % (row.get("orderid") or ""),
                payload=row,
                synapse_ref=str(row.get("orderid") or ""),
            )
        self.last_shipment_sync = end_dt
        return len(rows)

    def pull_shipments(self, begin_date=None, end_date=None):
        """Fetch shipped orders from Synapse and store them inline (direct path)."""
        self.ensure_one()
        Shipment = self.env["synapse.shipment"]
        result = Shipment
        rows, end_dt = self._fetch_shipment_rows(begin_date, end_date)
        for row in rows:
            result |= Shipment._upsert_from_synapse(row, self)
        self.last_shipment_sync = end_dt
        return result

    @api.model
    def cron_pull_shipments(self):
        for connection in self.search([("active", "=", True)]):
            try:
                connection.enqueue_shipments()
            except UserError as exc:
                _logger.error(
                    "Synapse enqueue_shipments failed for %s: %s", connection.name, exc
                )
                continue

    # ------------------------------------------------------------------
    # Reconciliation: fix historical sends/receipts that never synced, and
    # force Odoo's on-hand back in line with Synapse/Freeport's real counts.
    # ------------------------------------------------------------------
    def reconcile_inventory(self):
        """Force Odoo's on-hand to match Synapse for every matched line whose
        (location-scoped) variance is nonzero — regardless of the connection's
        ``auto_reconcile_inventory`` toggle, since this is an explicit "fix it
        now" action rather than the passive auto-sync. Returns the corrections
        made (item, product, old Odoo qty, new Synapse qty, delta)."""
        self.ensure_one()
        lines = self.env["synapse.inventory.line"].search(
            [("connection_id", "=", self.id), ("matched", "=", True)]
        )
        mismatched = lines.filtered(lambda l: abs(l.variance) > 1e-6)
        corrections = [
            {
                "item": line.item,
                # .name, not .display_name: display_name is "[item] name" and
                # the item code already has its own column — showing it twice
                # made the Item and Product columns look identical.
                "product": line.product_id.name,
                "odoo_qty": line.odoo_qty,
                "synapse_qty": line.qty,
                "delta": line.variance,
            }
            for line in mismatched
        ]
        mismatched._reconcile_to_odoo()
        return corrections

    def _reconcile_stuck_orders(self):
        """Retry failed order/resync/shipment jobs once; return
        ``{order: [reason, ...]}`` for whatever is still broken afterward, or
        has no retryable job at all (e.g. the job was already vacuumed but
        the order was left in a Failed state).

        Only orders that are still confirmed and flagged to send are
        considered (matches ``SynapseQueueJob._is_retryable_stuck_job``) — a
        quotation that was never actually sent to Synapse (or was
        cancelled/unflagged after a failed send) is left alone instead of
        being retried and re-reported every day forever."""
        self.ensure_one()
        still_failed = self.env["synapse.queue.job"]._retry_stuck_order_jobs()
        order_reasons = {}
        for job in still_failed:
            order = job._linked_sale_order()
            if not order:
                continue
            order_reasons.setdefault(order, []).append(
                "%s: %s" % (job.name, job.message or _("Unknown error"))
            )
        linked_ids = [order.id for order in order_reasons]
        orphaned = self.env["sale.order"].search(
            [
                ("synapse_status", "=", "failed"),
                ("synapse_to_send", "=", True),
                ("state", "in", ["sale", "done"]),
                ("id", "not in", linked_ids or [0]),
            ]
        )
        for order in orphaned:
            order_reasons.setdefault(order, []).append(
                _(
                    "Synapse status is Failed but no active retryable sync "
                    "job was found; needs manual review (Resync / Send to "
                    "Synapse)."
                )
            )
        return order_reasons

    def _send_reconcile_order_email(self, order, reasons):
        """One order needs a human: email its salesperson AND fulfillment
        together (both addressed on the same message), per the required
        notification routing — via the 'Synapse: Reconcile Order Issue'
        mail.template (bha_synapse.mail_template_synapse_reconcile_order).
        Recipients live on the template itself (salesperson through
        partner_to, fulfillment through email_to), editable from Settings >
        Technical > Email Templates. Never raises."""
        template = self.env.ref(
            "bha_synapse.mail_template_synapse_reconcile_order",
            raise_if_not_found=False,
        )
        if not template:
            _logger.error(
                "Synapse reconcile: order issue email template not found"
            )
            return
        try:
            template.with_context(reasons=list(reasons)).send_mail(
                order.id, force_send=True, raise_exception=False,
            )
        except Exception:  # noqa: BLE001 - a notification failure must not break reconciliation
            _logger.exception(
                "Synapse reconcile: could not email order %s", order.name
            )

    def _send_reconcile_inventory_email(self, corrections):
        """One summary to fulfillment for every inventory item corrected this
        run (no sales rep — an inventory line isn't tied to one order), via
        the 'Synapse: Reconcile Inventory Summary' mail.template
        (bha_synapse.mail_template_synapse_reconcile_inventory). Never
        raises."""
        if not corrections:
            return
        self.ensure_one()
        template = self.env.ref(
            "bha_synapse.mail_template_synapse_reconcile_inventory",
            raise_if_not_found=False,
        )
        if not template:
            _logger.error(
                "Synapse reconcile: inventory email template not found"
            )
            return
        rows = [
            {
                "item": line["item"] or "",
                "product": line["product"] or "",
                "odoo_qty": "%.2f" % line["odoo_qty"],
                "synapse_qty": "%.2f" % line["synapse_qty"],
                "delta": "%.2f" % line["delta"],
            }
            for line in corrections
        ]
        try:
            template.with_context(
                correction_count=len(rows), correction_rows=rows
            ).send_mail(
                self.id, force_send=True, raise_exception=False,
            )
        except Exception:  # noqa: BLE001 - a notification failure must not break reconciliation
            _logger.exception(
                "Synapse reconcile: could not send the inventory summary email"
            )

    def _run_synapse_reconciliation(self):
        """Full reconciliation pass for this connection: retry stuck
        sends/receipts and notify whoever needs to act, then force-correct
        inventory variances and notify fulfillment. Never raises — this must
        not break the cron or the dashboard button that calls it."""
        self.ensure_one()
        order_count = correction_count = 0
        try:
            order_reasons = self._reconcile_stuck_orders()
            order_count = len(order_reasons)
            for order, reasons in order_reasons.items():
                self._send_reconcile_order_email(order, reasons)
            corrections = self.reconcile_inventory()
            correction_count = len(corrections)
            self._send_reconcile_inventory_email(corrections)
        except Exception:
            _logger.exception("Synapse reconciliation failed for %s", self.name)
        return {"orders": order_count, "corrections": correction_count}

    @api.model
    def cron_reconcile_synapse(self):
        for connection in self.search([("active", "=", True)]):
            connection._run_synapse_reconciliation()

    def action_reconcile_synapse(self):
        """Dashboard/connection button: run reconciliation now."""
        self.ensure_one()
        result = self._run_synapse_reconciliation()
        return self._notify(
            _(
                "Reconciliation done: %(orders)s order issue(s) notified, "
                "%(corrections)s inventory item(s) corrected."
            )
            % result
        )

    # ------------------------------------------------------------------
    # Attachments (encode + transfer)
    # ------------------------------------------------------------------
    def list_attachments(self, orderid=None, shipid=None, loadno=None, rows=100):
        """Look up attachments stored in Synapse for an order/ship/load."""
        self.ensure_one()
        filters = {}
        if orderid is not None:
            filters["orderid"] = {"value": str(orderid), "condition": "equals"}
        if shipid is not None:
            filters["shipid"] = {"value": str(shipid), "condition": "equals"}
        if loadno is not None:
            filters["loadno"] = {"value": str(loadno), "condition": "equals"}
        payload = {"rows": rows}
        if filters:
            payload["filters"] = filters
        data = self._call("attachments", payload)
        return data.get("rows", []) if isinstance(data, dict) else []

    def download_attachment(self, file_id):
        """Download a single attachment; returns raw bytes."""
        self.ensure_one()
        return self._call("attachments/download", {"file_id": file_id}, raw=True)

    def upload_attachment(self, attachment, orderid=None, shipid=None, loadno=None):
        """Upload an ir.attachment to Synapse against an order or load.

        Confirmed contract (verified live 2026-06): POST /attachments/upload as
        ``multipart/form-data`` with form fields ``orderid`` + ``shipid`` (or
        ``loadno``) and a file part named ``file`` (filename and mimetype are
        taken from the part). Returns the API response, e.g.
        ``{"status": "OKAY", "file_id": ["01K..."]}``.
        """
        self.ensure_one()
        data = {}
        if loadno is not None:
            data["loadno"] = str(loadno)
        else:
            if orderid is None:
                raise UserError(_("orderid (and shipid) or loadno is required to upload."))
            data["orderid"] = str(orderid)
            data["shipid"] = str(shipid if shipid is not None else 1)
        # ir.attachment.datas is base64 text; Synapse wants the raw bytes.
        raw = base64.b64decode(attachment.datas) if attachment.datas else b""
        files = {
            "file": (
                attachment.name or "attachment",
                raw,
                attachment.mimetype or "application/octet-stream",
            )
        }
        return self._call_multipart("attachments/upload", data=data, files=files)

    def fetch_attachments_to_order(self, order):
        """Download every Synapse attachment for an order into Odoo."""
        self.ensure_one()
        if not order.synapse_orderid:
            raise UserError(_("Order %s has no Synapse order id.") % order.name)
        rows = self.list_attachments(
            orderid=order.synapse_orderid, shipid=order.synapse_shipid
        )
        Attachment = self.env["ir.attachment"]
        created = Attachment
        for row in rows:
            file_id = row.get("file_id")
            if not file_id:
                continue
            existing = Attachment.search(
                [
                    ("res_model", "=", "sale.order"),
                    ("res_id", "=", order.id),
                    ("description", "=", "synapse:%s" % file_id),
                ],
                limit=1,
            )
            if existing:
                continue
            content = self.download_attachment(file_id)
            created |= Attachment.create(
                {
                    "name": row.get("filename") or file_id,
                    "datas": base64.b64encode(content),
                    "res_model": "sale.order",
                    "res_id": order.id,
                    "mimetype": row.get("mimetype") or "application/octet-stream",
                    "description": "synapse:%s" % file_id,
                }
            )
        return created

    def _auto_send_packing_slip(self, order):
        """Automatically generate and upload packing slip to Synapse Anywhere after order is sent."""
        self.ensure_one()
        if not self.auto_send_packing_slip:
            return
        if order.synapse_customer_packing_list:
            return
        if not order.synapse_orderid:
            _logger.info(
                "Skipping auto Synapse packing slip for order %s: order has not been sent to Synapse yet.",
                order.name,
            )
            return
        try:
            self.upload_order_attachments(order)
            order.message_post(
                body=_(
                    "Packing slip automatically generated and uploaded to Synapse Anywhere."
                )
            )
        except Exception as exc:
            _logger.exception(
                "Failed to auto-upload packing slip to Synapse for order %s",
                order.name,
            )
            self.env["synapse.queue.job"]._enqueue(
                self,
                "attachment",
                "export",
                name=_("Auto Upload Packing Slip %s") % order.name,
                res_model="sale.order",
                res_id=order.id,
                synapse_ref=order.synapse_orderid,
            )
            order.message_post(
                body=_(
                    "Order %(order)s was sent to Synapse, but automatic upload of packing slip encountered an error: %(error)s. An upload job has been queued."
                )
                % {"order": order.name, "error": str(exc)}
            )

    def _generate_packing_slip(self, order, force=False):
        """Render Odoo's Delivery Slip (packing slip) for the order's outgoing
        pickings and store it as an ``ir.attachment`` on the sale order so the
        upload path below can push it to Synapse.

        Odoo's packing slip is an on-demand *report* on the ``stock.picking``,
        not a saved file on the sale order, so without this the upload finds
        nothing to send. Idempotent: if a packing slip is already attached to
        the order (and not marked as already uploaded, unless force=True), it
        is reused rather than regenerated, so repeated uploads don't create
        duplicates. If an already-uploaded packing slip exists and force=True,
        its PDF is regenerated and cleared of the uploaded prefix.

        Orders flagged as 'Customer Packing List' never get a generated
        packing slip — the customer supplies their own document."""
        self.ensure_one()
        Attachment = self.env["ir.attachment"]
        if order.synapse_customer_packing_list:
            return Attachment
        outgoing = order.picking_ids.filtered(
            lambda p: p.picking_type_code == "outgoing" and p.state != "cancel"
        )
        if not outgoing:
            return Attachment
        # Prioritize ready/assigned or completed pickings so backorders waiting for stock are excluded
        ready = outgoing.filtered(lambda p: p.state in ("assigned", "done"))
        pickings = ready or outgoing
        name = "Packing Slip - %s.pdf" % order.name
        existing = Attachment.search(
            [
                ("res_model", "=", "sale.order"),
                ("res_id", "=", order.id),
                ("name", "=", name),
            ],
            limit=1,
        )
        if existing and not force and not (existing.description or "").startswith("synapse_up:"):
            return existing
        pdf, _content_type = self.env["ir.actions.report"]._render_qweb_pdf(
            "stock.report_deliveryslip", pickings.ids
        )
        if existing:
            existing.write({
                "datas": base64.b64encode(pdf),
                "description": False,
            })
            return existing
        return Attachment.create(
            {
                "name": name,
                "datas": base64.b64encode(pdf),
                "res_model": "sale.order",
                "res_id": order.id,
                "mimetype": "application/pdf",
            }
        )

    def action_send_pending_packing_slips(self):
        """Send packing slips for confirmed Synapse orders that have outgoing deliveries."""
        self.ensure_one()
        orders = self.env["sale.order"].search([
            ("synapse_connection_id", "=", self.id),
            ("synapse_orderid", "!=", False),
            ("synapse_customer_packing_list", "=", False),
            ("state", "in", ["sale", "done"]),
            ("picking_ids.state", "!=", "cancel"),
            ("picking_ids.picking_type_code", "=", "outgoing"),
        ])
        count = 0
        for order in orders:
            try:
                uploaded = self.upload_order_attachments(order)
                if uploaded:
                    count += 1
            except Exception as e:
                _logger.warning("Could not upload packing slip for order %s: %s", order.name, e)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "sticky": False,
                "message": _("Packing slip upload processed for %s order(s).") % count,
            },
        }

    def upload_order_attachments(self, order):
        """Upload an order's Odoo attachments (shipping labels, packing slips,
        warehouse docs) to Synapse. Attachments that came *from* Synapse
        (``synapse:*``) or were already uploaded (``synapse_up:*``) are skipped.
        Requires the order to have been sent (so it has a Synapse order id).

        The Odoo Delivery Slip (packing slip) is generated on the fly and
        attached to the order first (see :meth:`_generate_packing_slip`) so it
        is included in the upload. Orders flagged 'Customer Packing List' skip
        the packing slip entirely; only labels and manual documents go up."""
        self.ensure_one()
        if not order.synapse_orderid:
            raise UserError(
                _("Order %s has not been sent to Synapse yet; cannot upload "
                  "attachments.") % order.name
            )
        # Make sure the packing slip exists as an attachment before scanning.
        self._generate_packing_slip(order)
        Attachment = self.env["ir.attachment"]
        # Scan both the sale order (packing slip, manual docs) AND its outgoing
        # delivery pickings, because carrier-generated shipping labels are
        # attached to the stock.picking (res_model='stock.picking'), not the
        # sale order. This is what lets one upload send labels + packing slip.
        pickings = order.picking_ids.filtered(
            lambda p: p.picking_type_code == "outgoing"
        )
        domain = ["|",
            "&", ("res_model", "=", "sale.order"), ("res_id", "=", order.id),
            "&", ("res_model", "=", "stock.picking"),
            ("res_id", "in", pickings.ids),
        ]
        atts = Attachment.search(domain)
        uploaded = Attachment
        packing_slip_name = "Packing Slip - %s.pdf" % order.name
        for att in atts:
            desc = att.description or ""
            if desc.startswith("synapse:") or desc.startswith("synapse_up:"):
                continue
            if order.synapse_customer_packing_list and att.name == packing_slip_name:
                # Customer packing list: skip ours even if it was generated
                # before the flag was set on the order.
                continue
            if not att.datas:
                continue
            result = self.upload_attachment(
                att, orderid=order.synapse_orderid, shipid=order.synapse_shipid
            )
            # Mark as uploaded so it is skipped next time. Record the returned
            # file_id (response gives it as a list) for traceability; the
            # "synapse_up:" prefix is what the skip check above looks for.
            file_id = result.get("file_id") if isinstance(result, dict) else None
            if isinstance(file_id, list):
                file_id = file_id[0] if file_id else None
            att.description = "synapse_up:%s" % (file_id or desc or "")
            uploaded |= att
        return uploaded

