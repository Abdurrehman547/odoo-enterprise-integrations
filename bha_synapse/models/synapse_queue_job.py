# -*- coding: utf-8 -*-
import json
import logging
from datetime import timedelta

from markupsafe import escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SynapseQueueJob(models.Model):
    _name = "synapse.queue.job"
    _description = "Synapse Sync Queue Job"
    _order = "id desc"
    _rec_name = "name"

    name = fields.Char(string="Name", required=True, default="Synapse Job")
    connection_id = fields.Many2one(
        "synapse.connection",
        string="Connection",
        required=True,
        ondelete="cascade",
        index=True,
    )
    job_type = fields.Selection(
        [
            ("inventory", "Inventory"),
            ("shipment", "Shipment"),
            ("order", "Order"),
            ("update", "Update Order"),
            ("resync", "Resync Order"),
            ("attachment", "Attachment"),
            ("cancel", "Cancel Order"),
            ("purchase_order", "Purchase Order"),
            ("purchase_resync", "Resync Purchase Order"),
            ("purchase_cancel", "Cancel Purchase Order"),
        ],
        string="Type",
        required=True,
        index=True,
    )
    direction = fields.Selection(
        [("import", "Synapse → Odoo"), ("export", "Odoo → Synapse")],
        string="Direction",
        required=True,
    )
    state = fields.Selection(
        [
            ("new", "New"),
            ("progress", "In Progress"),
            ("done", "Done"),
            ("failed", "Failed"),
        ],
        string="State",
        default="new",
        required=True,
        index=True,
    )
    payload = fields.Text(
        string="Payload",
        help="JSON data for the job: the Synapse row for imports, or the "
        "order_type / line specification for an order export.",
    )
    res_model = fields.Char(string="Odoo Model")
    res_id = fields.Integer(string="Odoo Record ID")
    synapse_ref = fields.Char(string="Synapse Reference", index=True)
    message = fields.Text(string="Last Message")
    attempts = fields.Integer(string="Attempts", default=0)
    last_attempt = fields.Datetime(string="Last Attempt")

    # Fixed (classid, objid) key for the Postgres advisory lock that serialises
    # queue processing so the cron and a manual "Process" click never drain the
    # same job twice. Arbitrary but unique to this queue.
    _KS_QUEUE_LOCK = (8741, 2)

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------
    @api.model
    def _enqueue(
        self,
        connection,
        job_type,
        direction,
        name=None,
        payload=None,
        res_model=None,
        res_id=None,
        synapse_ref=None,
    ):
        """Create a queue job and log the enqueue."""
        job = self.create(
            {
                "name": name or "%s %s" % (dict(self._fields["job_type"].selection).get(job_type, job_type), direction),
                "connection_id": connection.id,
                "job_type": job_type,
                "direction": direction,
                "payload": json.dumps(payload) if payload is not None else False,
                "res_model": res_model,
                "res_id": res_id,
                "synapse_ref": synapse_ref,
                "state": "new",
            }
        )
        self.env["synapse.log"]._log(
            "success",
            _("Job enqueued: %s") % job.name,
            connection=connection,
            queue_job=job,
            job_type=job_type,
            direction=direction,
            name=_("Enqueued %s") % job.name,
        )
        return job

    # ------------------------------------------------------------------
    # State helpers (commit so progress is visible / not lost on crash)
    # ------------------------------------------------------------------
    def _set_progress(self):
        self.write({"state": "progress", "attempts": self.attempts + 1,
                    "last_attempt": fields.Datetime.now()})
        self.env.cr.commit()

    def _set_done(self, message=None):
        self.write({"state": "done", "message": message or _("OK")})
        self.env.cr.commit()

    def _set_failed(self, message=None):
        # The error that brought us here may have aborted the transaction; roll
        # back first so the state write + commit can succeed.
        self.env.cr.rollback()
        self.write({"state": "failed", "message": message or _("Failed")})
        self.env.cr.commit()

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------
    def process(self):
        """Process the jobs in ``self`` one by one (dispatch by type/direction)."""
        for job in self:
            if job.state == "done":
                continue
            job._set_progress()
            try:
                message = job._dispatch()
                job._set_done(message)
                self.env["synapse.log"]._log(
                    "success",
                    message or _("Processed %s") % job.name,
                    connection=job.connection_id,
                    queue_job=job,
                    job_type=job.job_type,
                    direction=job.direction,
                    res_ref=job.synapse_ref,
                )
                # Per-order notification: email the outcome of every order push.
                # Success chatter for a send is posted by push_order itself.
                if job.job_type in (
                    "order", "resync", "purchase_order", "purchase_resync",
                ):
                    order = job._linked_record()
                    if order:
                        job._send_status_email(order, _("Sent"))
            except Exception as exc:  # noqa: BLE001 - any failure marks the job failed
                _logger.exception("Synapse queue job %s failed", job.id)
                msg = str(exc)
                job._set_failed(msg)
                self.env["synapse.log"]._log(
                    "failed",
                    msg,
                    connection=job.connection_id,
                    queue_job=job,
                    job_type=job.job_type,
                    direction=job.direction,
                    res_ref=job.synapse_ref,
                )
                order = job._linked_record()
                if order:
                    # Make the failure visible on the order itself: chatter with
                    # the reason, plus a Failed synapse_status for send jobs
                    # (update/cancel failures keep the current status — the
                    # order is still correctly in Synapse).
                    if job.job_type in (
                        "order", "resync", "purchase_order", "purchase_resync",
                    ):
                        order.write({"synapse_status": "failed"})
                    reason_html = str(escape(msg or _("Unknown error"))).replace(
                        "\n", "<br/>"
                    )
                    order.message_post(
                        body=_(
                            "Synapse job “%(job)s” FAILED:<br/>%(reason)s"
                        )
                        % {"job": job.name, "reason": reason_html}
                    )
                    job._send_status_email(order, _("Failed"), reason=msg)
                    # Persist the failure traces like the module's other state
                    # writes, so they survive a later crash in this run.
                    self.env.cr.commit()
        return True

    def _linked_sale_order(self):
        """The sale.order this job acts on, or an empty recordset."""
        self.ensure_one()
        if self.res_model != "sale.order" or not self.res_id:
            return self.env["sale.order"]
        return self.env["sale.order"].browse(self.res_id).exists()

    def _linked_purchase_order(self):
        """The purchase.order this job acts on, or an empty recordset."""
        self.ensure_one()
        if self.res_model != "purchase.order" or not self.res_id:
            return self.env["purchase.order"]
        return self.env["purchase.order"].browse(self.res_id).exists()

    def _linked_record(self):
        """The order (sale or purchase) this job acts on, or an empty
        recordset. Used by process()'s per-order side effects so they work
        for outbound (sale) and inbound (purchase) jobs alike."""
        self.ensure_one()
        return self._linked_sale_order() or self._linked_purchase_order()

    def _send_status_email(self, order, outcome, reason=None):
        """Send the Synapse order-status email (sent / failed + reason).

        Recipients: every user in the 'Synapse Order Notifications' group —
        and only them. Never raises — a notification problem must not fail
        queue processing. The delivery outcome is always reported on the
        order's chatter (sent / not sent + reason)."""
        self.ensure_one()
        # PO and SO each have their own template (they bind to different
        # models); pick by the order being reported on.
        template_xmlid = (
            "bha_synapse.email_template_synapse_purchase_status"
            if order._name == "purchase.order"
            else "bha_synapse.email_template_synapse_order_status"
        )
        template = self.env.ref(template_xmlid, raise_if_not_found=False)
        group = self.env.ref(
            "bha_synapse.group_synapse_notification", raise_if_not_found=False
        )
        partners = (
            group.users.partner_id.filtered(lambda p: p.email)
            if group else self.env["res.partner"]
        )
        if not template:
            self._post_email_note(
                order, outcome, False, _("Synapse notification mail template "
                                         "not found")
            )
            return
        if not partners:
            note = _("the 'Synapse Order Notifications' group has no users "
                     "with an email address")
            _logger.info(
                "Synapse status email skipped for %s: %s", order.name, note
            )
            self._post_email_note(order, outcome, False, note)
            return
        try:
            mail_id = template.with_context(
                synapse_outcome=outcome,
                synapse_reason=reason or "",
            ).send_mail(
                order.id, force_send=True, raise_exception=False,
                # recipient_ids (not partner_ids): mail.mail delivers only to
                # email_to / recipient_ids. partner_ids would land on the
                # underlying mail.message via _inherits and the mail would go
                # out with no recipient. email_values are merged after the
                # template values, so this overrides them as intended.
                email_values={"recipient_ids": [(6, 0, partners.ids)]},
            )
        except Exception as exc:  # noqa: BLE001 - notifications must not break the queue
            _logger.exception(
                "Synapse status email could not be sent for order %s", order.name
            )
            self._post_email_note(order, outcome, False, str(exc) or repr(exc))
            return
        # The template has auto_delete: a delivered mail is unlinked right
        # away, so a record that survives the send means delivery failed and
        # carries the reason on it.
        mail = self.env["mail.mail"].browse(mail_id).exists() if mail_id else False
        if mail_id and not mail:
            self._post_email_note(order, outcome, True, partners=partners)
        elif mail and mail.state == "sent":
            self._post_email_note(order, outcome, True, partners=partners)
        else:
            note = (
                (mail and (mail.failure_reason or mail.state))
                or _("the email could not be generated (template rendering "
                     "failed; see the server log)")
            )
            self._post_email_note(order, outcome, False, note)

    def _post_email_note(self, order, outcome, delivered, note=False, partners=None):
        """Post the notification-email delivery outcome on the order's chatter.

        ``delivered`` True/False with an optional ``note`` (reason), or the
        ``partners`` the mail went to. Never raises — a chatter problem must
        not fail queue processing either."""
        try:
            if delivered:
                order.message_post(
                    body=_(
                        "Synapse notification email “%(outcome)s” sent to "
                        "%(count)s recipient(s) in the 'Synapse Order "
                        "Notifications' group: %(names)s"
                    )
                    % {
                        "outcome": outcome,
                        "count": len(partners),
                        "names": escape(", ".join(partners.mapped("name"))),
                    }
                )
            else:
                reason_html = str(escape(note or _("Unknown error"))).replace(
                    "\n", "<br/>"
                )
                order.message_post(
                    body=_(
                        "Synapse notification email “%(outcome)s” NOT sent — "
                        "%(reason)s"
                    )
                    % {"outcome": outcome, "reason": reason_html}
                )
        except Exception:  # noqa: BLE001
            _logger.exception(
                "Could not post the email status note on order %s", order.name
            )

    def _dispatch(self):
        """Run the real work for one job. Returns a short status message."""
        self.ensure_one()
        connection = self.connection_id
        data = json.loads(self.payload) if self.payload else {}

        if self.job_type == "inventory" and self.direction == "import":
            line = self.env["synapse.inventory.line"]._import_row(data, connection)
            return _("Inventory item %s synced.") % (line.item or "")

        if self.job_type == "shipment" and self.direction == "import":
            shipment = self.env["synapse.shipment"]._upsert_from_synapse(data, connection)
            return _("Shipment %s synced.") % (shipment.orderid or "")

        if self.job_type == "order" and self.direction == "export":
            order = self.env["sale.order"].browse(self.res_id).exists()
            if not order:
                raise UserError(_("Sale order for this job no longer exists."))
            line_values = self._rebuild_line_values(order, data.get("lines"))
            result = connection.push_order(
                order,
                order_type=data.get("order_type"),
                line_values=line_values,
                shipping_service=data.get("shipping_service"),
                carrier_code=data.get("carrier_code"),
                priority=data.get("priority"),
            )
            return _("Order %s sent. Synapse order id: %s") % (
                order.name,
                result.get("orderid"),
            )

        if self.job_type == "update" and self.direction == "export":
            order = self.env["sale.order"].browse(self.res_id).exists()
            if not order:
                raise UserError(_("Sale order for this job no longer exists."))
            connection.update_order(
                order,
                shipping_service=data.get("shipping_service"),
                carrier_code=data.get("carrier_code"),
                extra=data.get("extra"),
            )
            return _("Order %s updated in Synapse.") % order.name

        if self.job_type == "resync" and self.direction == "export":
            order = self.env["sale.order"].browse(self.res_id).exists()
            if not order:
                raise UserError(_("Sale order for this job no longer exists."))
            line_values = self._rebuild_line_values(order, data.get("lines"))
            result = connection.resync_order(
                order,
                order_type=data.get("order_type"),
                line_values=line_values,
                shipping_service=data.get("shipping_service"),
                carrier_code=data.get("carrier_code"),
                priority=data.get("priority"),
            )
            return _("Order %s resynced. New Synapse order id: %s") % (
                order.name,
                result.get("orderid"),
            )

        if self.job_type == "cancel" and self.direction == "export":
            order = self.env["sale.order"].browse(self.res_id).exists()
            if not order:
                raise UserError(_("Sale order for this job no longer exists."))
            connection.cancel_order(order)
            return _("Order %s cancelled in Synapse.") % order.name

        if self.job_type == "purchase_order" and self.direction == "export":
            po = self.env["purchase.order"].browse(self.res_id).exists()
            if not po:
                raise UserError(_("Purchase order for this job no longer exists."))
            line_values = self._rebuild_line_values(po, data.get("lines"))
            result = connection.push_inbound_order(
                po,
                order_type=data.get("order_type"),
                line_values=line_values,
                priority=data.get("priority"),
            )
            return _("Purchase order %s sent. Synapse order id: %s") % (
                po.name,
                result.get("orderid"),
            )

        if self.job_type == "purchase_resync" and self.direction == "export":
            po = self.env["purchase.order"].browse(self.res_id).exists()
            if not po:
                raise UserError(_("Purchase order for this job no longer exists."))
            result = connection.resync_inbound_order(
                po, order_type=data.get("order_type")
            )
            return _("Purchase order %s resynced. New Synapse order id: %s") % (
                po.name,
                result.get("orderid"),
            )

        if self.job_type == "purchase_cancel" and self.direction == "export":
            po = self.env["purchase.order"].browse(self.res_id).exists()
            if not po:
                raise UserError(_("Purchase order for this job no longer exists."))
            connection.cancel_order(po)
            return _("Purchase order %s cancelled in Synapse.") % po.name

        if self.job_type == "attachment" and self.direction == "import":
            order = self.env["sale.order"].browse(self.res_id).exists()
            if not order:
                raise UserError(_("Sale order for this job no longer exists."))
            created = connection.fetch_attachments_to_order(order)
            return _("%s attachment(s) downloaded for %s.") % (len(created), order.name)

        if self.job_type == "attachment" and self.direction == "export":
            order = self.env["sale.order"].browse(self.res_id).exists()
            if not order:
                raise UserError(_("Sale order for this job no longer exists."))
            uploaded = connection.upload_order_attachments(order)
            return _("%s attachment(s) uploaded for %s.") % (len(uploaded), order.name)

        raise UserError(
            _("Unsupported job: %s / %s") % (self.job_type, self.direction)
        )

    def _rebuild_line_values(self, order, lines):
        """Turn the stored line payload back into push_order's line_values.

        ``lines`` is a list of {product_id, qty, uom}. When absent (None) we let
        push_order derive lines from the order itself.
        """
        if not lines:
            return None
        Product = self.env["product.product"]
        values = []
        for ln in lines:
            product = Product.browse(ln.get("product_id")).exists()
            if not product:
                continue
            values.append(
                {"product": product, "qty": ln.get("qty"), "uom": ln.get("uom")}
            )
        return values or None

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------
    def action_process(self):
        """Process the selected jobs immediately (respecting the queue lock)."""
        self.process()
        return True

    def action_requeue(self):
        """Reset failed jobs back to 'new' so the next run retries them."""
        self.filtered(lambda j: j.state == "failed").write({"state": "new"})
        return True

    def process_now(self):
        """Process ``self`` immediately (same as the manual 'Process' button)
        instead of waiting for the queue's 10-minute cron. Used right after
        enqueueing so actions like 'Send to Synapse' give instant feedback."""
        self.process()

    def _is_retryable_stuck_job(self):
        """Whether a failed job is still worth an automatic retry.

        Shipment (import) jobs aren't gated — they don't depend on a sale
        order's send state. Order/resync (export) jobs are only retried while
        the linked order is still a confirmed order flagged to send (the same
        gating ``cron_push_orders`` uses to decide what to send in the first
        place). Without this, a job that failed on a quotation that was never
        actually sent — or on an order later cancelled/unflagged — gets reset
        to 'new' and retried by the reconcile cron every single day forever,
        re-failing and re-posting the same chatter/email noise indefinitely."""
        self.ensure_one()
        if self.job_type not in (
            "order", "resync", "purchase_order", "purchase_resync",
        ):
            return True
        if self.job_type in ("purchase_order", "purchase_resync"):
            # Inbound mirror: retry while the PO is still a confirmed order
            # flagged to send (purchase states instead of sale states).
            po = self._linked_purchase_order()
            if not po:
                return False
            return bool(po.synapse_to_send) and po.state in ("purchase", "done")
        order = self._linked_sale_order()
        if not order:
            return False
        return bool(order.synapse_to_send) and order.state in ("sale", "done")

    @api.model
    def _retry_stuck_order_jobs(self):
        """Reset failed order/resync/shipment jobs to 'new' and retry them
        once. Returns whichever are STILL failed afterward — those are the
        ones that need a human, as opposed to a transient API hiccup that a
        retry alone fixes. Used by the Synapse reconciliation job.

        Only jobs that pass :meth:`_is_retryable_stuck_job` are retried — see
        there for why."""
        stuck = self.search(
            [
                ("state", "=", "failed"),
                ("job_type", "in", (
                    "order", "resync", "shipment",
                    "purchase_order", "purchase_resync",
                )),
            ]
        )
        if not stuck:
            return stuck
        retryable = stuck.filtered(lambda j: j._is_retryable_stuck_job())
        if not retryable:
            return self.browse()
        retryable.write({"state": "new"})
        retryable.process()
        return retryable.filtered(lambda j: j.state == "failed")

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------
    @api.model
    def cron_process_queue(self, limit=500):
        """Drain pending jobs: every 'new' job (freshly enqueued), plus any
        'failed' job that's still worth an automatic retry (see
        :meth:`_is_retryable_stuck_job` — this stops a failed send tied to a
        quotation that was never confirmed/flagged from being re-attempted,
        and re-failing/re-posting to chatter, on every run of this cron).
        Serialised with a session advisory lock so the cron and a manual
        click never process the same job twice."""
        self.env.cr.execute("SELECT pg_try_advisory_lock(%s, %s)", self._KS_QUEUE_LOCK)
        got_lock = self.env.cr.fetchone()[0]
        if not got_lock:
            _logger.info("Synapse queue already being processed; skipping this run.")
            return
        try:
            candidates = self.search([("state", "in", ["new", "failed"])], limit=limit)
            jobs = candidates.filtered(
                lambda j: j.state == "new" or j._is_retryable_stuck_job()
            )
            jobs.process()
        finally:
            try:
                self.env.cr.execute(
                    "SELECT pg_advisory_unlock(%s, %s)", self._KS_QUEUE_LOCK
                )
            except Exception:  # noqa: BLE001
                self.env.cr.rollback()
                self.env.cr.execute(
                    "SELECT pg_advisory_unlock(%s, %s)", self._KS_QUEUE_LOCK
                )

    @api.model
    def cron_vacuum_jobs(self, days=7):
        """Delete done jobs older than ``days`` to keep counts meaningful."""
        limit_date = fields.Datetime.now() - timedelta(days=days)
        old = self.search(
            [("state", "=", "done"), ("write_date", "<", limit_date)]
        )
        old.unlink()
        return True

