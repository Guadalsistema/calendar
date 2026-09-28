from odoo import _, api, models
from odoo.exceptions import ValidationError


class ResourceBookingCombination(models.Model):
    _inherit = "resource.booking.combination"

    def _get_location_resource(self):
        """Return the (zero-or-one) location resource for this combination."""
        self.ensure_one()
        return self.resource_ids.filtered(lambda resource: resource.resource_type == "location")

    def _get_location_address(self):
        """Return the formatted address for consumers, or an empty string."""
        location = self._get_location_resource()
        return location.location_partner_id.contact_address if location else ""

    @api.constrains("resource_ids")
    def _check_location_count(self):
        for combination in self:
            if len(combination.resource_ids.filtered(lambda resource: resource.resource_type == "location")) > 1:
                raise ValidationError(_("A resource combination can contain at most one location."))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._check_location_count()
        return records

    def write(self, vals):
        if "resource_ids" in vals:
            bookings = self.env["resource.booking"].sudo().search([
                ("combination_id", "in", self.ids),
                ("state", "in", ["scheduled", "confirmed"]),
            ])
            if bookings:
                raise ValidationError(_(
                    "A combination used by scheduled or confirmed appointments cannot change its resources."
                ))
        result = super().write(vals)
        return result
