from odoo import api, fields, models
from odoo.exceptions import AccessError


class ResPartner(models.Model):
    _inherit = "res.partner"

    barbershop_private_booking_customer = fields.Boolean(
        string="Private barbershop booking customer",
        copy=False,
        index=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        self._check_barber_private_customer_write(vals_list)
        return super().create(vals_list)

    def write(self, vals):
        if "barbershop_private_booking_customer" in vals or self.filtered(
            "barbershop_private_booking_customer"
        ):
            self._check_barber_private_customer_write([vals])
        return super().write(vals)

    def _check_barber_private_customer_write(self, vals_list):
        user = self.env.user
        if user.has_group("barbershop_booking_privacy.group_barber") and not user.has_group(
            "barbershop_booking_privacy.group_manager"
        ):
            if any("barbershop_private_booking_customer" in vals for vals in vals_list) or self.filtered(
                "barbershop_private_booking_customer"
            ):
                raise AccessError(self.env._("Barbers cannot modify private customers."))
