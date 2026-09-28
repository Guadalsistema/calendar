from odoo import api, models
from odoo.exceptions import AccessError


class ResourceBooking(models.Model):
    _inherit = "resource.booking"

    @api.model_create_multi
    def create(self, vals_list):
        self._check_barber_read_only()
        return super().create(vals_list)

    def write(self, vals):
        self._check_barber_read_only()
        return super().write(vals)

    def unlink(self):
        self._check_barber_read_only()
        return super().unlink()

    def action_confirm(self):
        self._check_barber_read_only()
        return super().action_confirm()

    def action_unschedule(self):
        self._check_barber_read_only()
        return super().action_unschedule()

    def action_cancel(self):
        self._check_barber_read_only()
        return super().action_cancel()

    def _check_barber_read_only(self):
        user = self.env.user
        if user.has_group("barbershop_booking_privacy.group_barber") and not user.has_group(
            "barbershop_booking_privacy.group_manager"
        ):
            raise AccessError(self.env._("Barbers can only read assigned bookings."))


class ResourceCalendar(models.Model):
    _inherit = "resource.calendar"

    def _calendar_event_busy_intervals(self, start_dt, end_dt, resource, analyzed_booking_id):
        # Return only availability intervals; hidden events remain hidden from callers.
        return super(ResourceCalendar, self.sudo())._calendar_event_busy_intervals(
            start_dt, end_dt, resource, analyzed_booking_id
        )
