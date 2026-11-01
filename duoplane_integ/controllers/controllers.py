# -*- coding: utf-8 -*-
# from odoo import http


# class DuoplaneInteg(http.Controller):
#     @http.route('/duoplane_integ/duoplane_integ', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/duoplane_integ/duoplane_integ/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('duoplane_integ.listing', {
#             'root': '/duoplane_integ/duoplane_integ',
#             'objects': http.request.env['duoplane_integ.duoplane_integ'].search([]),
#         })

#     @http.route('/duoplane_integ/duoplane_integ/objects/<model("duoplane_integ.duoplane_integ"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('duoplane_integ.object', {
#             'object': obj
#         })

