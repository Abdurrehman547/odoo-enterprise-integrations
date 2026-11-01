from odoo import models, fields, api


class ResPartner(models.Model):
    _inherit = 'res.partner'

    x_duoplane_customer = fields.Boolean(
        string='Duoplane Customer',
        default=False,
        copy=False,
        help='Partner created from Duoplane integration'
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Override create to mark partners created by Duoplane"""
        partners = super().create(vals_list)

        # Check if created from Duoplane context
        if self.env.context.get('duoplane_import'):
            partners.write({'x_duoplane_customer': True})

        return partners