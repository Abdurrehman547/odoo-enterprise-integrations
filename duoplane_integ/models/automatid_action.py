# -*- coding: utf-8 -*-
import openpyxl
import logging
import os
from odoo import models, api
from odoo.modules.module import get_module_resource

_logger = logging.getLogger(__name__)


class ProductRetailerSkuImport(models.TransientModel):
    _name = 'product.retailer.sku.import'
    _description = 'Import Retailer SKU from Excel'

    def cron_import_retailer_sku(self):
        """Scheduled action to import retailer SKUs"""

        # Define the Excel file path
        excel_file_path = get_module_resource(
            'duoplane_integ',
            'static/xlsx',
            'Texas_Distribution_SKUs.xlsx'
        )

        if not excel_file_path or not os.path.exists(excel_file_path):
            _logger.error(f"Excel file not found: {excel_file_path}")
            return

        try:
            # Load Excel file
            wb = openpyxl.load_workbook(excel_file_path)
            ws = wb.active

            stats = {
                'total_rows': 0,
                'products_updated': 0,
                'products_not_found': 0,
                'multiple_matches': 0,
                'errors': 0
            }

            # Process each row (skip header)
            for row_num, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
                try:
                    with self.env.cr.savepoint():
                        if not row or len(row) < 5:
                            continue

                        retailer_sku = row[3]  # Column 4: retailer_sku
                        vendor_sku = row[4]  # Column 5: vendor_sku

                        if not retailer_sku or not vendor_sku:
                            continue

                        stats['total_rows'] += 1

                        # Convert vendor_sku to string and clean it
                        vendor_sku_str = str(vendor_sku).strip()

                        # Remove decimal point if it's a float
                        if vendor_sku_str.endswith('.0'):
                            vendor_sku_str = vendor_sku_str[:-2]

                        # Search PRODUCT VARIANT (product.product) by barcode OR default_code
                        products = self.env['product.product'].search([
                            '|',
                            ('barcode', '=', vendor_sku_str),
                            ('default_code', '=', vendor_sku_str)
                        ])

                        if not products:
                            stats['products_not_found'] += 1
                            _logger.warning(
                                f"Row {row_num}: Product variant not found for vendor_sku: {vendor_sku_str}")
                            continue

                        if len(products) > 1:
                            stats['multiple_matches'] += 1
                            _logger.warning(f"Row {row_num}: Multiple products found for vendor_sku: {vendor_sku_str}")
                            # Update all matching variants
                            for product in products:
                                product.write({'x_retailer_sku': retailer_sku})
                                _logger.info(
                                    f"Row {row_num}: Updated product variant {product.id} - {product.display_name} with retailer SKU {retailer_sku}")
                            stats['products_updated'] += len(products)
                            continue

                        product = products[0]

                        # Update retailer SKU
                        old_sku = product.x_retailer_sku
                        if old_sku != retailer_sku:
                            product.write({'x_retailer_sku': retailer_sku})
                            stats['products_updated'] += 1
                            _logger.info(
                                f"Row {row_num}: Updated product variant {product.default_code} (barcode: {product.barcode}) with retailer SKU {retailer_sku} (was: {old_sku})")
                        else:
                            _logger.debug(
                                f"Row {row_num}: Product {product.default_code} already has correct retailer SKU {retailer_sku}")

                except Exception as e:
                    stats['errors'] += 1
                    _logger.error(f"Row {row_num}: {str(e)}")

            _logger.info(f"Retailer SKU import completed: {stats}")

        except Exception as e:
            _logger.error(f"Error in scheduled retailer SKU import: {str(e)}")