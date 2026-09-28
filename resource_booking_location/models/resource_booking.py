from odoo import api, models


class ResourceBooking(models.Model):
    _inherit = "resource.booking"

    @api.depends(
        "combination_id.resource_ids",
        "combination_id.resource_ids.resource_type",
        "combination_id.resource_ids.location_partner_id.contact_address",
        "meeting_id.location",
        "type_id",
    )
    def _compute_location(self):
        super()._compute_location()
        for booking in self:
            address = booking.combination_id._get_location_address() if booking.combination_id else ""
            if address:
                booking.location = address

    def _prepare_meeting_vals(self):
        values = super()._prepare_meeting_vals()
        address = self.combination_id._get_location_address() if self.combination_id else ""
        if address:
            values["location"] = address
        return values
