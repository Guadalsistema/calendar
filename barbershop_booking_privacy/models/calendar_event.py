from odoo import api, models
from odoo.exceptions import AccessError


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    @api.model_create_multi
    def create(self, vals_list):
        if any(vals.get("resource_booking_ids") for vals in vals_list):
            self._check_barber_read_only()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("resource_booking_ids") or self.sudo().filtered("resource_booking_ids"):
            self._check_barber_read_only()
        return super().write(vals)

    def unlink(self):
        if self.sudo().filtered("resource_booking_ids"):
            self._check_barber_read_only()
        return super().unlink()

    def _check_barber_read_only(self):
        user = self.env.user
        if user.has_group("barbershop_booking_privacy.group_barber") and not user.has_group(
            "barbershop_booking_privacy.group_manager"
        ):
            raise AccessError(self.env._("Barbers cannot modify booking calendar events."))


class MailFollowers(models.Model):
    _inherit = "mail.followers"

    def read(self, fields=None, load="_classic_read"):
        user = self.env.user
        if user.has_group("barbershop_booking_privacy.group_barber") and not user.has_group(
            "barbershop_booking_privacy.group_manager"
        ):
            for follower in self.sudo():
                if follower.res_model in {"resource.booking", "calendar.event", "res.partner"}:
                    self.env[follower.res_model].browse(follower.res_id).check_access("read")
        return super().read(fields=fields, load=load)
