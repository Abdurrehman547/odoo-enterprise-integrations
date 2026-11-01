from odoo import models, fields, api, _
from odoo.exceptions import UserError


class DuoplaneSyncWizard(models.TransientModel):
    _name = 'duoplane.sync.wizard'
    _description = 'Duoplane Sync Wizard'

    config_id = fields.Many2one(
        'duoplane.config',
        string='Configuration',
        required=True,
        default=lambda self: self.env['duoplane.config'].search([], limit=1)
    )
    sync_date_from = fields.Datetime(
        string='Sync From Date',
        default=fields.Datetime.now,
        required=True
    )
    sync_unfulfilled_only = fields.Boolean(
        string='Unfulfilled Orders Only',
        default=True
    )

    def action_sync(self):
        """Execute sync"""
        self.ensure_one()

        if not self.config_id:
            raise UserError(_('Please select a configuration'))

        # Update config sync settings temporarily
        original_date = self.config_id.sync_date_from
        original_unfulfilled = self.config_id.sync_unfulfilled_only

        self.config_id.write({
            'sync_date_from': self.sync_date_from,
            'sync_unfulfilled_only': self.sync_unfulfilled_only
        })

        # Run sync
        result = self.config_id.sync_orders()

        # Restore original settings
        self.config_id.write({
            'sync_date_from': original_date,
            'sync_unfulfilled_only': original_unfulfilled
        })

        # Show result
        message = f"Sync completed!\n\n"
        message += f"Orders created: {result['orders_created']}\n"
        message += f"Products created: {result['products_created']}\n"
        message += f"Partners created: {result['partners_created']}\n"
        message += f"Errors: {result['errors']}"

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Sync Complete'),
                'message': message,
                'type': 'success' if result['errors'] == 0 else 'warning',
                'sticky': True,
            }
        }