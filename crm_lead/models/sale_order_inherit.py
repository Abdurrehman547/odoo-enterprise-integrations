from odoo import models, fields, api
class SaleOrder(models.Model):
    _inherit = 'sale.order'

    is_salesperson_restricted = fields.Boolean(
        compute="_compute_is_salesperson_restricted",
        store=False
    )

    @api.depends_context('uid')
    def _compute_is_salesperson_restricted(self):
        # ⚠️ IMPORTANT: Replace 'BaghouseAmerica' with your exact module folder name
        group_xml_id = 'crm_lead.group_no_salesperson_change'

        for rec in self:
            rec.is_salesperson_restricted = self.env.user.has_group(group_xml_id)