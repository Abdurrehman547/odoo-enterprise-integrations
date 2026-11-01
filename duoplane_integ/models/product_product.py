from odoo import models, fields,api

class ProductProduct(models.Model):
    _inherit = 'product.product'

    x_duoplane_product = fields.Boolean(
        string='From Duoplane',
        default=False,
        copy=False,
        help='Product created from Duoplane integration'
    )
    x_retailer_sku = fields.Char(
        string='Retailer SKU',
        copy=False,
        help='SKU from the retailer system'
    )


    @api.model_create_multi
    def create(self, vals_list):
        """Override create to mark products created by Duoplane"""
        products = super().create(vals_list)

        # Check if created from Duoplane context
        if self.env.context.get('duoplane_import'):
            products.write({'x_duoplane_product': True})

        return products


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    x_retailer_sku = fields.Char(
        string='Retailer SKU',
        copy=False,
        index=True,
        help='Retailer SKU from Excel import'
    )