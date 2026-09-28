from pytz import all_timezones_set

from odoo import _, api, models
from odoo.exceptions import ValidationError

from odoo.addons.resource.models.utils import Intervals


class ResourceCalendar(models.Model):
    _inherit = "resource.calendar"

    @api.constrains("attendance_ids", "global_leave_ids", "leave_ids", "tz")
    def _check_bookings_scheduling(self):
        result = super()._check_bookings_scheduling()
        bookings = self.env["resource.booking"].sudo().search([
            ("combination_id.availability_calendar_id", "in", self.ids),
        ])
        bookings._check_scheduling()
        return result

    @api.constrains("tz")
    def _check_location_resource_timezone(self):
        locations = self.env["resource.resource"].search([
            ("resource_type", "=", "location"), ("calendar_id", "in", self.ids)
        ])
        for calendar in self:
            if locations.filtered(lambda resource: resource.calendar_id == calendar) and (
                calendar.tz not in all_timezones_set
            ):
                raise ValidationError(_("A location calendar requires a valid timezone."))

    @api.model
    def _calendar_event_busy_intervals(self, start_dt, end_dt, resource, analyzed_booking_id):
        # Location capacity is unlimited: ignore only booking-derived conflicts.
        if resource and resource.resource_type == "location":
            return Intervals([])
        return super()._calendar_event_busy_intervals(start_dt, end_dt, resource, analyzed_booking_id)
