from odoo.http import request, route

from odoo.addons.resource_booking.controllers.portal import CustomerPortal


class BarbershopCustomerPortal(CustomerPortal):
    def _barbershop_portal_can_modify(self):
        user = request.env.user
        return not user.has_group(
            "barbershop_booking_privacy.group_barber"
        ) or user.has_group("barbershop_booking_privacy.group_manager")

    def _barbershop_booking_for_portal_write(self, booking_id):
        if not request.env.user._is_internal():
            return None
        booking = request.env["resource.booking"].browse(booking_id).exists()
        if not booking:
            raise request.not_found()
        booking.check_access("read")
        return booking.with_context(
            using_portal=True, tz=booking.type_id.resource_calendar_id.tz
        )

    def _booking_get_page_view_values(self, booking_sudo, access_token, **kwargs):
        values = super()._booking_get_page_view_values(
            booking_sudo, access_token, **kwargs
        )
        values["barbershop_portal_can_modify"] = (
            self._barbershop_portal_can_modify()
        )
        return values

    @route()
    def portal_booking_cancel(self, booking_id, access_token=None, **kwargs):
        booking = self._barbershop_booking_for_portal_write(booking_id)
        if booking:
            booking.action_cancel()
            return request.redirect("/my")
        return super().portal_booking_cancel(booking_id, access_token, **kwargs)

    @route()
    def portal_booking_confirm(
        self, booking_id, access_token, when, combination_id=None, **kwargs
    ):
        booking = self._barbershop_booking_for_portal_write(booking_id)
        if booking:
            booking.check_access("write")
            booking._check_barber_read_only()
        return super().portal_booking_confirm(
            booking_id, access_token, when, combination_id, **kwargs
        )
