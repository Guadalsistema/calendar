from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from pytz import all_timezones_set


def _remove_location(resources):
    """Convert resources to Odoo's standard material kind on addon removal."""
    resources.with_context(allow_location_booking_cleanup=True).write({"resource_type": "material"})


class ResourceResource(models.Model):
    _inherit = "resource.resource"

    resource_type = fields.Selection(
        selection_add=[("location", "Location")],
        ondelete={"location": _remove_location},
    )
    location_partner_id = fields.Many2one(
        "res.partner", string="Location address", ondelete="restrict"
    )
    partner_latitude = fields.Float(
        related="location_partner_id.partner_latitude", readonly=False, digits=(10, 7)
    )
    partner_longitude = fields.Float(
        related="location_partner_id.partner_longitude", readonly=False, digits=(10, 7)
    )
    image_1920 = fields.Image(string="Location Image", attachment=True)

    def init(self):
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS resource_booking_location_active_partner_company_uniq
            ON resource_resource (location_partner_id, COALESCE(company_id, 0))
            WHERE resource_type = 'location' AND active
        """)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("resource_type") == "location" and vals.get("active", True):
                company_id = vals.get("company_id") or self.env.company.id
                if self.with_context(active_test=False).search_count([
                    ("resource_type", "=", "location"),
                    ("location_partner_id", "=", vals.get("location_partner_id")),
                    ("company_id", "=", company_id),
                    ("active", "=", True),
                ]):
                    raise ValidationError(_("Only one location resource may use an address per company."))
        return super().create(vals_list)

    @api.constrains("resource_type", "location_partner_id", "company_id", "calendar_id", "active")
    def _check_location_data(self):
        for resource in self:
            if resource.resource_type != "location":
                continue
            partner = resource.location_partner_id
            if not partner or partner.type != "other":
                raise ValidationError(_("A location requires an Other Address contact."))
            if partner.company_id and partner.company_id != resource.company_id:
                raise ValidationError(_("The address must belong to the resource company."))
            if not resource.calendar_id:
                raise ValidationError(_("A location requires a working calendar."))
            if resource.calendar_id.tz not in all_timezones_set:
                raise ValidationError(_("A location calendar requires a valid timezone."))
            other = self.with_context(active_test=False).search([
                ("id", "!=", resource.id), ("resource_type", "=", "location"),
                ("location_partner_id", "=", partner.id),
                ("company_id", "=", resource.company_id.id),
                ("active", "=", True),
            ], limit=1)
            if resource.active and other:
                raise ValidationError(_("Only one location resource may use an address per company."))

    @api.constrains("partner_latitude", "partner_longitude")
    def _check_location_coordinates(self):
        for resource in self:
            if resource.resource_type != "location":
                continue
            if resource.partner_latitude and not -90 <= resource.partner_latitude <= 90:
                raise ValidationError(_("Latitude must be between -90 and 90."))
            if resource.partner_longitude and not -180 <= resource.partner_longitude <= 180:
                raise ValidationError(_("Longitude must be between -180 and 180."))

    def write(self, vals):
        guarded_fields = {"resource_type", "location_partner_id", "company_id", "calendar_id", "active"}
        locations = self.filtered(lambda resource: resource.resource_type == "location")
        if vals.get("resource_type") == "location":
            locations |= self
        if not self.env.context.get("allow_location_booking_cleanup") and locations and guarded_fields.intersection(vals):
            bookings = self.env["resource.booking"].sudo().search([
                ("combination_id.resource_ids", "in", locations.ids),
                ("state", "in", ["scheduled", "confirmed"]),
            ], limit=1)
            if bookings:
                raise ValidationError(_(
                    "A location used by scheduled or confirmed appointments cannot be changed."
                ))
        result = super().write(vals)
        if "resource_type" in vals:
            self.env["resource.booking.combination"].search([
                ("resource_ids", "in", self.ids)
            ])._check_location_count()
        return result


class ResPartner(models.Model):
    _inherit = "res.partner"

    @api.constrains("partner_latitude", "partner_longitude", "type", "company_id")
    def _check_location_coordinates(self):
        locations = self.env["resource.resource"].sudo().search([
            ("resource_type", "=", "location"),
            ("location_partner_id", "in", self.ids),
        ])
        partners = self & locations.mapped("location_partner_id")
        if any(
            not -90 <= partner.partner_latitude <= 90
            or not -180 <= partner.partner_longitude <= 180
            for partner in partners
        ):
            raise ValidationError(_("Location coordinates are outside valid ranges."))
        for resource in locations:
            if resource.location_partner_id.type != "other":
                raise ValidationError(_("A location requires an Other Address contact."))
            if resource.location_partner_id.company_id and resource.location_partner_id.company_id != resource.company_id:
                raise ValidationError(_("The address must belong to the resource company."))
