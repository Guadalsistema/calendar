import re
from datetime import datetime, timedelta, timezone

import pytz
from psycopg2.errors import SerializationFailure

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BookingChoice(models.AbstractModel):
    """Generic shop-choice API, independent of HTTP and website publication."""

    _name = "barbershop.booking.choice"
    _description = "Barbershop booking choice service"

    @api.model
    def _request_context(self, company_id, type_id, mode, location_id):
        """Validate scalar identifiers and return company/service/candidates."""
        try:
            company = self.env["res.company"].browse(int(company_id)).exists()
            booking_type = self.env["resource.booking.type"].browse(int(type_id)).exists()
        except (TypeError, ValueError):
            raise ValidationError(_("Invalid booking service or company."))
        if not company or not booking_type:
            raise ValidationError(_("Invalid booking service or company."))
        candidates = booking_type._barbershop_choice_combinations(
            company, mode, location_id
        )
        return company, booking_type, candidates

    @api.model
    def eligible_combinations(self, company_id, type_id, mode=None, location_id=None):
        """Return active choices as a resource.booking.combination recordset."""
        return self._request_context(company_id, type_id, mode, location_id)[2]

    @api.model
    def _slots_for(self, booking_type, candidates, start_dt, end_dt):
        """Map each candidate ID to its available OCA date-to-datetimes map."""
        if not isinstance(start_dt, datetime) or not isinstance(end_dt, datetime):
            raise ValidationError(_("A timezone-aware slot range is required."))
        if start_dt.tzinfo is None or end_dt.tzinfo is None or end_dt <= start_dt:
            raise ValidationError(_("A timezone-aware slot range is required."))
        probe = self.env["resource.booking"].new({"type_id": booking_type.id})
        result = {}
        for combo in candidates:
            result[combo.id] = probe._get_available_slots(start_dt, end_dt, combo)
        return result

    @api.model
    def available_slots(self, company_id, type_id, mode=None, location_id=None,
                        start_dt=None, end_dt=None, combination_id=None):
        """Return ``{combination_id: {date: [aware slot datetimes]}}``.

        Each entry preserves its candidate identity; any-candidate consumers may
        union the values, while an explicit combination receives only its slots.
        """
        company, booking_type, candidates = self._request_context(
            company_id, type_id, mode, location_id
        )
        if combination_id:
            try:
                chosen = candidates.filtered(lambda combo: combo.id == int(combination_id))
            except (TypeError, ValueError):
                chosen = candidates.browse()
            if not chosen:
                raise ValidationError(_("Invalid resource combination."))
            candidates = chosen
        return self._slots_for(booking_type, candidates, start_dt, end_dt)

    @api.model
    def _validated_contact(self, contact):
        """Accept only normalized, bounded customer contact values."""
        if not isinstance(contact, dict):
            raise ValidationError(_("Invalid contact details."))
        name = " ".join(str(contact.get("name") or "").split())
        email = str(contact.get("email") or "").strip().lower()
        phone = " ".join(str(contact.get("phone") or "").split())
        if (not name or len(name) > 200 or len(email) > 254
                or (email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email))
                or not phone or len(phone) > 64):
            raise ValidationError(
                _("Valid name and phone are required; email must be valid when provided.")
            )
        return {"name": name, "email": email, "phone": phone}

    @api.model
    def _lock_candidates(self, candidates):
        """Acquire deterministic locks covering exclusive and empty candidates."""
        all_resources = candidates.mapped("resource_ids").filtered(
            lambda resource: resource.resource_type != "location"
        )
        exclusive_ids = all_resources.ids
        # Serialize empty/location-only choices too when candidates are mixed.
        no_exclusive = candidates.filtered(
            lambda combo: not combo.resource_ids.filtered(
                lambda resource: resource.resource_type != "location"
            )
        )
        if exclusive_ids:
            self.env.cr.execute(
                "SELECT id FROM resource_resource WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
                [sorted(exclusive_ids)],
            )
            # Under PostgreSQL REPEATABLE READ, a waiter keeps its pre-lock
            # snapshot. Bump the locked tuple version so a stale waiter gets a
            # serialization failure instead of approving stale availability.
            self.env.cr.execute(
                "UPDATE resource_resource SET name = name WHERE id = ANY(%s)",
                [sorted(exclusive_ids)],
            )
        combination_ids = no_exclusive.ids if exclusive_ids else candidates.ids
        if combination_ids:
            self.env.cr.execute(
                "SELECT id FROM resource_booking_combination WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
                [sorted(combination_ids)],
            )
            if exclusive_ids:
                self.env.cr.execute(
                    "UPDATE resource_booking_combination SET name = name WHERE id = ANY(%s)",
                    [sorted(combination_ids)],
                )

    @api.model
    def confirm_booking(self, company_id, type_id, mode=None, location_id=None,
                        combination_id=None, when=None, contact=None):
        """Lock, revalidate and confirm a booking; invoke inside caller savepoint.

        ``combination_id`` may be false to choose the first free candidate by ID.
        ``when`` is timezone-aware; errors precede partner/booking creation.
        """
        values = self._validated_contact(contact)
        if not isinstance(when, datetime) or when.tzinfo is None or when.utcoffset() is None:
            raise ValidationError(_("Invalid booking date."))
        company, booking_type, candidates = self._request_context(
            company_id, type_id, mode, location_id
        )
        exact = False
        if combination_id:
            try:
                exact = candidates.filtered(lambda combo: combo.id == int(combination_id))
            except (TypeError, ValueError):
                exact = candidates.browse()
            if not exact:
                raise ValidationError(_("Invalid resource combination."))
        try:
            with self.env.cr.savepoint():
                self._lock_candidates(candidates)
        except SerializationFailure:
            raise ValidationError(_("The chosen schedule is no longer available."))
        # Rows may have changed while waiting. Validate the full selection again.
        company, booking_type, candidates = self._request_context(
            company_id, type_id, mode, location_id
        )
        values = self._validated_contact(contact)
        if not isinstance(when, datetime) or when.tzinfo is None or when.utcoffset() is None:
            raise ValidationError(_("Invalid booking date."))
        if exact and exact.id not in candidates.ids:
            raise ValidationError(_("Invalid resource combination."))
        local_when = when.astimezone(pytz.timezone(booking_type.resource_calendar_id.tz))
        duration = booking_type.duration
        end_when = local_when + timedelta(hours=duration)
        slots_by_candidate = self._slots_for(
            booking_type, candidates, local_when, end_when
        )
        ordered = exact or candidates.sorted("id")
        chosen = ordered.filtered(
            lambda combo: local_when in slots_by_candidate.get(combo.id, {}).get(
                local_when.date(), []
            )
        )[:1]
        if not chosen:
            raise ValidationError(_("The chosen schedule is no longer available."))
        start = when.astimezone(timezone.utc).replace(tzinfo=None)
        partner = self.env["res.partner"].create({
            **values,
            "barbershop_private_booking_customer": True,
        })
        booking = self.env["resource.booking"].create({
            "type_id": booking_type.id,
            "partner_ids": [(4, partner.id)],
            "start": fields.Datetime.to_string(start),
            "duration": duration,
            "combination_auto_assign": False,
            "combination_id": chosen.id,
        })
        booking.action_confirm()
        return booking
