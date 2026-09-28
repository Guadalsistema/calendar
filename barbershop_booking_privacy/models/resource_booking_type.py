from odoo import _, api, models
from odoo.exceptions import ValidationError


class ResourceBookingType(models.Model):
    _inherit = "resource.booking.type"

    def _barbershop_location_choices(self, company):
        """Return active, company-valid locations offered by this service."""
        self.ensure_one()
        combinations = self.combination_rel_ids.filtered(
            lambda rel: rel.combination_id.active
            and not rel.combination_id.forced_calendar_id
        ).mapped("combination_id")
        combinations = combinations.filtered(
            lambda combo: all(
                resource.active and resource.company_id in (False, company)
                for resource in combo.resource_ids
            )
        )
        resources = combinations.mapped("resource_ids").filtered(
            lambda resource: resource.active
            and resource.resource_type == "location"
            and resource.company_id == company
        )
        return resources

    def _barbershop_resolve_choice(self, company, mode, location_id, choices):
        """Validate the mode/location pair without selecting a fallback."""
        Location = self.env["resource.resource"]
        if mode is None:
            if choices:
                raise ValidationError(_("Choose a booking location or independent service."))
            return "independent", Location
        if mode == "independent" and not location_id:
            return mode, Location
        if mode != "location":
            raise ValidationError(_("Invalid booking choice mode."))
        try:
            selected = Location.browse(int(location_id)).exists() if location_id else Location
        except (TypeError, ValueError):
            selected = Location
        if selected not in choices:
            raise ValidationError(_("Invalid booking location."))
        return mode, selected

    @api.model
    def _barbershop_combination_matches(self, combo, company, mode, location):
        """Check company/resource eligibility and the requested choice kind."""
        if any(
            not resource.active or resource.company_id not in (False, company)
            for resource in combo.resource_ids
        ):
            return False
        locations = combo.resource_ids.filtered(
            lambda resource: resource.resource_type == "location"
        )
        if mode == "location":
            return location in locations and len(locations) == 1 and not combo.forced_calendar_id
        return not locations

    def _barbershop_choice_combinations(self, company, mode=None, location_id=None):
        """Resolve active combinations for a validated location/independent choice."""
        self.ensure_one()
        Combination = self.env["resource.booking.combination"]
        if not self.active or self.company_id not in (False, company):
            raise ValidationError(_("Invalid booking service or company."))
        location_choices = self._barbershop_location_choices(company)
        mode, selected_location = self._barbershop_resolve_choice(
            company, mode, location_id, location_choices
        )
        combos = self.combination_rel_ids.filtered(
            lambda rel: rel.combination_id.active
        ).mapped("combination_id")
        result = combos.filtered(
            lambda combo: self._barbershop_combination_matches(
                combo, company, mode, selected_location
            )
        )
        if not result:
            raise ValidationError(_("No eligible resource combinations."))
        return result
