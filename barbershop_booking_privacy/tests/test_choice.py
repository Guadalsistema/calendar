from datetime import datetime, timedelta

from odoo import Command, fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase
from pytz import UTC, timezone

from odoo.addons.resource_booking.tests.common import create_test_data


class TestBookingChoice(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        create_test_data(cls)
        cls.api = cls.env["barbershop.booking.choice"]

    def _location(self, name, company=None, active=True, calendar=None, tz="UTC"):
        address = self.env["res.partner"].create({
            "name": name, "type": "other", "street": "1 Main Street",
        })
        return self.env["resource.resource"].create({
            "name": name, "resource_type": "location", "location_partner_id": address.id,
            "calendar_id": (calendar or self.r_calendars[2]).id, "tz": tz,
            "company_id": (company or self.env.company).id, "active": active,
        })

    def _service(self, combinations):
        service = self.env["resource.booking.type"].create({
            "name": "Choice service", "company_id": self.env.company.id,
            "resource_calendar_id": self.rbt.resource_calendar_id.id,
            "duration": 1, "slot_duration": 1,
        })
        service.write({"combination_rel_ids": [
            Command.create({"combination_id": combo.id}) for combo in combinations
        ]})
        return service

    def test_location_and_independent_candidates_and_omitted_mode(self):
        shop1, shop2 = self._location("Shop one"), self._location("Shop two")
        located = self.env["resource.booking.combination"].create([
            {"resource_ids": [Command.set([shop.id, self.r_materials[index].id])]}
            for shop, index in ((shop1, 0), (shop2, 1))
        ])
        independent = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([self.r_materials[2].id])],
        })
        service = self._service(located | independent)
        with self.assertRaises(ValidationError):
            self.api.eligible_combinations(self.env.company.id, service.id)
        result = self.api.eligible_combinations(
            self.env.company.id, service.id, "location", shop1.id
        )
        self.assertEqual(result, located[:1])
        self.assertEqual(
            self.api.eligible_combinations(
                self.env.company.id, service.id, "independent"
            ), independent,
        )
        with self.assertRaises(ValidationError):
            self.api.eligible_combinations(
                self.env.company.id, service.id, "location", 99999999
            )

    def test_independent_forced_calendar_and_legacy_mode(self):
        combo = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([self.r_materials[0].id])],
            "forced_calendar_id": self.r_calendars[1].id,
        })
        service = self._service(combo)
        self.assertEqual(
            self.api.eligible_combinations(self.env.company.id, service.id), combo
        )

    def test_slot_result_preserves_candidate_and_service(self):
        choices = self.env["resource.booking.combination"].create([
            {"resource_ids": [Command.set([self.r_materials[i].id])]} for i in (0, 1)
        ])
        service = self._service(choices)
        start = datetime.now(UTC) + timedelta(days=7)
        start = start.replace(hour=8, minute=0, second=0, microsecond=0)
        result = self.api.available_slots(
            self.env.company.id, service.id, "independent", start_dt=start,
            end_dt=start + timedelta(days=1),
        )
        self.assertEqual(set(result), set(choices.ids))
        self.assertTrue(all(isinstance(value, dict) for value in result.values()))

    def test_confirmation_rejects_before_partner_creation_and_confirms_manual_choice(self):
        combo = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([self.r_materials[0].id])],
        })
        service = self._service(combo)
        values = {"name": "  Choice   Customer ", "email": "Choice@EXAMPLE.org",
                  "phone": "+1 555 123 4567"}
        partner_count = self.env["res.partner"].search_count([("email", "=", "choice@example.org")])
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.api.confirm_booking(
                self.env.company.id, service.id, "independent", combination_id=False,
                when=datetime.now(), contact=values,
            )
        self.assertEqual(
            self.env["res.partner"].search_count([("email", "=", "choice@example.org")]),
            partner_count,
        )
        # Use a future working slot on the resource calendar.
        day = datetime.today().date()
        day += timedelta(days=(7 - day.weekday()) % 7 or 7)
        when = UTC.localize(datetime.combine(day, datetime.min.time()).replace(hour=10))
        booking = self.api.confirm_booking(
            self.env.company.id, service.id, "independent", combination_id=combo.id,
            when=when, contact=values,
        )
        self.assertEqual(booking.combination_id, combo)
        self.assertFalse(booking.combination_auto_assign)
        self.assertTrue(booking.partner_ids.barbershop_private_booking_customer)
        self.assertTrue(booking.get_portal_url())

    def test_any_available_selects_deterministically_and_exact_mismatch_fails(self):
        combos = self.env["resource.booking.combination"].create([
            {"resource_ids": [Command.set([self.r_materials[i].id])]} for i in (0, 1)
        ])
        service = self._service(combos)
        day = datetime.today().date()
        day += timedelta(days=(7 - day.weekday()) % 7 or 7)
        when = UTC.localize(datetime.combine(day, datetime.min.time()).replace(hour=10))
        booking = self.api.confirm_booking(
            self.env.company.id, service.id, "independent", combination_id=False,
            when=when, contact={"name": "Any", "email": "any@example.org", "phone": "123456"},
        )
        self.assertEqual(booking.combination_id, combos.sorted("id")[:1])
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.api.confirm_booking(
                self.env.company.id, service.id, "independent", combination_id=999999,
                when=when, contact={"name": "Bad", "email": "bad@example.org", "phone": "123456"},
            )

    def test_invalid_company_mode_location_service_and_empty_candidates(self):
        shop = self._location("Valid shop")
        material_combo = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([self.r_materials[0].id])],
        })
        service = self._service(material_combo)
        foreign_company = self.env["res.company"].create({"name": "Choice foreign company"})
        foreign_shop = self._location("Foreign shop", company=foreign_company)
        inactive_shop = self._location("Inactive shop", active=False)
        located_combo = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([shop.id, self.r_materials[1].id])],
        })
        service.write({"combination_rel_ids": [
            Command.create({"combination_id": located_combo.id}),
        ]})
        cases = (
            (self.env.company.id, service.id, "wrong", None),
            (self.env.company.id, service.id, "location", "not-an-id"),
            (self.env.company.id, service.id, "location", foreign_shop.id),
            (self.env.company.id, service.id, "location", inactive_shop.id),
            (foreign_company.id, service.id, "location", shop.id),
            (self.env.company.id, 99999999, "independent", None),
            ("bad", service.id, "independent", None),
        )
        for company_id, type_id, mode, location_id in cases:
            with self.subTest(company_id=company_id, type_id=type_id, mode=mode), self.assertRaises(ValidationError):
                self.api.eligible_combinations(company_id, type_id, mode, location_id)
        empty_service = self._service(self.env["resource.booking.combination"])
        with self.assertRaises(ValidationError):
            self.api.eligible_combinations(self.env.company.id, empty_service.id, "independent")

    def test_forced_location_is_not_advertised_and_exact_cross_service_rejected(self):
        shop = self._location("Forced shop")
        forced = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([shop.id])],
            "forced_calendar_id": self.r_calendars[1].id,
        })
        service = self._service(forced)
        # A located combination that OCA cannot honor is not a public shop
        # choice and must not force an explicit location selection.
        with self.assertRaises(ValidationError):
            self.api.eligible_combinations(self.env.company.id, service.id)
        with self.assertRaises(ValidationError):
            self.api.eligible_combinations(self.env.company.id, service.id, "location", shop.id)
        other = self._service(self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([self.r_materials[0].id])],
        }))
        day = datetime.today().date()
        day += timedelta(days=(7 - day.weekday()) % 7 or 7)
        when = UTC.localize(datetime.combine(day, datetime.min.time()).replace(hour=10))
        before = self.env["res.partner"].search_count([("email", "=", "cross@example.org")])
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.api.confirm_booking(
                self.env.company.id, other.id, "independent", combination_id=forced.id,
                when=when, contact={"name": "Cross", "email": "cross@example.org", "phone": "123456"},
            )
        self.assertEqual(self.env["res.partner"].search_count([("email", "=", "cross@example.org")]), before)

    def test_inactive_service_and_unavailable_slot_leave_no_records(self):
        combo = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([self.r_materials[0].id])],
        })
        service = self._service(combo)
        service.active = False
        with self.assertRaises(ValidationError):
            self.api.eligible_combinations(self.env.company.id, service.id, "independent")
        service.active = True
        partner_domain = [("email", "=", "unavailable@example.org")]
        booking_count = self.env["resource.booking"].search_count([("type_id", "=", service.id)])
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.api.confirm_booking(
                self.env.company.id, service.id, "independent", combination_id=combo.id,
                when=UTC.localize(datetime(2001, 1, 1, 2)),
                contact={"name": "Unavailable", "email": "unavailable@example.org", "phone": "123456"},
            )
        self.assertFalse(self.env["res.partner"].search_count(partner_domain))
        self.assertEqual(self.env["resource.booking"].search_count([("type_id", "=", service.id)]), booking_count)

    def test_non_utc_slots_obey_work_hours_and_location_leave(self):
        calendar = self.r_calendars[2].copy({"name": "Choice Los Angeles", "tz": "America/Los_Angeles"})
        shop = self._location("Los Angeles shop", calendar=calendar, tz="America/Los_Angeles")
        combo = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([shop.id])],
        })
        service = self._service(combo)
        service.resource_calendar_id = calendar
        zone = timezone("America/Los_Angeles")
        day = datetime.today().date()
        day += timedelta(days=(7 - day.weekday()) % 7 or 7)
        start = zone.localize(datetime.combine(day, datetime.min.time()))
        slots = self.api.available_slots(
            self.env.company.id, service.id, "location", shop.id,
            start_dt=start, end_dt=start + timedelta(days=1),
        )[combo.id]
        starts = [slot for date_slots in slots.values() for slot in date_slots]
        self.assertTrue(starts)
        self.assertTrue(all(slot.tzinfo.zone == "America/Los_Angeles" for slot in starts))
        self.assertTrue(all(slot.hour >= 8 for slot in starts), "calendar work hours must bound the slots")
        chosen = starts[0]
        start_utc = chosen.astimezone(UTC).replace(tzinfo=None)
        self.env["resource.calendar.leaves"].create({
            "name": "Choice location closure",
            "date_from": fields.Datetime.to_string(start_utc),
            "date_to": fields.Datetime.to_string(start_utc + timedelta(hours=1)),
            "calendar_id": calendar.id,
            "resource_id": shop.id,
        })
        after_leave = self.api.available_slots(
            self.env.company.id, service.id, "location", shop.id,
            start_dt=start, end_dt=start + timedelta(days=1),
        )[combo.id]
        self.assertNotIn(chosen, [slot for date_slots in after_leave.values() for slot in date_slots])
