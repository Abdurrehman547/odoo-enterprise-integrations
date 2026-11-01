/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { Component } from "@odoo/owl";

// Override the formatQuantity display to cap at 1000
const originalTemplate = odoo.loader.modules.get("@website_sale_stock/js/product_availability");