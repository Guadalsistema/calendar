from datetime import datetime, timedelta

from odoo import Command, fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase

from odoo.addons.resource_booking.tests.common import create_test_data
from odoo.addons.resource_booking.models.resource_calendar import (
    ResourceCalendar as OcaResourceCalendar,
)
from pytz import UTC


class ResourceBookingLocationCase(TransactionCase):
    def _location(self, name="Shop", partner=None, **values):
        partner = partner or self.env["res.partner"].create(
            {"name": name, "type": "other", "street": "1 Main Street"}
        )
        vals = {
            "name": name,
            "resource_type": "location",
            "location_partner_id": partner.id,
            "calendar_id": self.r_calendars[2].id,
            "tz": "UTC",
        }
        vals.update(values)
        return self.env["resource.resource"].create(vals)


class TestResourceBookingLocation(ResourceBookingLocationCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        create_test_data(cls)

    def _next_monday(self, hour=8):
        today = datetime.today().date()
        days = (7 - today.weekday()) % 7 or 7
        day = today + timedelta(days=days)
        return datetime.combine(day, datetime.min.time()).replace(hour=hour)

    def test_location_partner_validation_and_coordinates(self):
        ordinary = self.env["res.partner"].create({"name": "Person", "type": "contact"})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._location(partner=ordinary)
        shop = self.env["res.partner"].create({"name": "Shop", "type": "other"})
        location = self._location(partner=shop, partner_latitude=0, partner_longitude=0)
        self.assertEqual(location.partner_latitude, 0)
        location.partner_latitude = 40.1
        self.assertEqual(shop.partner_latitude, 40.1)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            location.partner_latitude = 91
        self.assertFalse(location.image_1920)

    def test_active_uniqueness_and_company(self):
        shop = self.env["res.partner"].create({"name": "Shop", "type": "other"})
        self._location(partner=shop)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._location(partner=shop)
        # Inactive duplicates are explicitly permitted.
        inactive = self._location(partner=shop, active=False)
        self.assertFalse(inactive.active)
        company = self.env.company
        foreign = self.env["res.partner"].create({
            "name": "Company shop", "type": "other", "company_id": company.id
        })
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._location(partner=foreign, company_id=False)

    def test_calendar_required_timezone_and_locationless_combination(self):
        shop = self.env["res.partner"].create({"name": "Shop", "type": "other"})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._location(partner=shop, calendar_id=False)
        calendar = self.r_calendars[2]
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            calendar.tz = "Not/A_Timezone"
            self._location(partner=shop)
        empty = self.env["resource.booking.combination"].create({"resource_ids": [Command.clear()]})
        self.assertFalse(empty._get_location_resource())
        self.assertEqual(empty._get_location_address(), "")

    def test_one_location_on_create_write_and_resource_type_change(self):
        first = self._location("First")
        second = self._location("Second")
        combo = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([first.id])]
        })
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            combo.write({"resource_ids": [Command.link(second.id)]})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.env["resource.booking.combination"].create({
                "resource_ids": [Command.set([first.id, second.id])]
            })
        # Changing the resource kind cannot evade the canonical membership check.
        second.write({"resource_type": "material"})
        combo.write({"resource_ids": [Command.link(second.id)]})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            second.write({"resource_type": "location"})

    def test_location_only_combination_and_booking_address(self):
        shop = self.env["res.partner"].create({
            "name": "Shop", "type": "other", "street": "1 Main Street"
        })
        location = self._location(partner=shop)
        combination = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([location.id])]
        })
        self.assertEqual(combination._get_location_resource(), location)
        self.assertIn("1 Main Street", combination._get_location_address())
        booking = self.env["resource.booking"].new({
            "partner_ids": [Command.link(self.partner.id)],
            "type_id": self.rbt.id,
            "combination_id": combination.id,
            "combination_auto_assign": False,
        })
        self.assertIn("1 Main Street", booking.location)
        self.assertEqual(booking._prepare_meeting_vals()["location"], booking.location)

    def test_simultaneous_appointments_share_shop_not_exclusive_resources(self):
        shop = self.env["res.partner"].create({
            "name": "Shop", "type": "other", "street": "1 Main Street"
        })
        location = self._location(partner=shop)
        combos = self.env["resource.booking.combination"].create([
            {"resource_ids": [Command.set([location.id, self.r_users[i].id, self.r_materials[i].id])]}
            for i in (0, 2)
        ])
        self.rbt.write({"combination_rel_ids": [
            Command.create({"combination_id": combo.id}) for combo in combos
        ]})
        start = self._next_monday()
        def schedule(combo, partner):
            return self.env["resource.booking"].create({
                "partner_ids": [Command.link(partner.id)],
                "type_id": self.rbt.id,
                "combination_id": combo.id,
                "combination_auto_assign": False,
                "start": fields.Datetime.to_string(start),
                "duration": 1,
            })
        first = schedule(combos[0], self.partner)
        other_partner = self.env["res.partner"].create({"name": "Second customer"})
        second = schedule(combos[1], other_partner)
        self.assertEqual(first.location, second.location)
        # A third appointment using the first exclusive resource still conflicts.
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            schedule(combos[0], self.env["res.partner"].create({"name": "Third"}))

    def test_linked_location_and_combination_are_immutable_with_live_booking(self):
        shop = self.env["res.partner"].create({"name": "Guarded shop", "type": "other"})
        location = self._location(partner=shop)
        combo = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([location.id, self.r_users[0].id])]
        })
        booking = self.env["resource.booking"].create({
            "partner_ids": [Command.link(self.partner.id)],
            "type_id": self.rbt.id,
            "combination_id": combo.id,
            "combination_auto_assign": False,
            "start": fields.Datetime.to_string(self._next_monday()),
            "duration": 1,
        })
        self.assertIn(booking.state, ("scheduled", "confirmed"))
        other_company = self.env["res.company"].create({"name": "Other company"})
        for label, mutation in (
            ("partner type", lambda: shop.write({"type": "contact"})),
            ("partner company", lambda: shop.write({"company_id": other_company.id})),
            ("calendar", lambda: location.write({"calendar_id": self.r_calendars[1].id})),
            ("company", lambda: location.write({"company_id": other_company.id})),
            ("type", lambda: location.write({"resource_type": "material"})),
            ("membership", lambda: combo.write({"resource_ids": [Command.set([location.id, self.r_users[1].id])]})),
        ):
            with self.subTest(mutation=label), self.assertRaises(ValidationError), self.env.cr.savepoint():
                mutation()

        material = self.env["resource.resource"].create({
            "name": "Booked material",
            "resource_type": "material",
            "calendar_id": self.r_calendars[0].id,
            "company_id": self.env.company.id,
        })
        material_combo = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set(material.ids)]
        })
        self.env["resource.booking"].create({
            "partner_ids": [Command.link(self.partner.id)],
            "type_id": self.rbt.id,
            "combination_id": material_combo.id,
            "combination_auto_assign": False,
            "start": fields.Datetime.to_string(self._next_monday()),
            "duration": 1,
        })
        new_shop = self.env["res.partner"].create({
            "name": "Converted shop", "type": "other"
        })
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            material.write({
                "resource_type": "location",
                "location_partner_id": new_shop.id,
            })

    def test_uninstall_callback_uses_normal_write_even_with_live_booking(self):
        shop = self.env["res.partner"].create({"name": "Cleanup shop", "type": "other"})
        location = self._location(partner=shop)
        combo = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([location.id, self.r_users[0].id])]
        })
        self.env["resource.booking"].create({
            "partner_ids": [Command.link(self.partner.id)], "type_id": self.rbt.id,
            "combination_id": combo.id, "combination_auto_assign": False,
            "start": fields.Datetime.to_string(self._next_monday()), "duration": 1,
        })
        from odoo.addons.resource_booking_location.models.resource_resource import _remove_location
        _remove_location(location)
        self.assertEqual(location.resource_type, "material")

    def test_location_calendar_and_leave_still_restrict_availability(self):
        shop = self.env["res.partner"].create({"name": "Shop", "type": "other"})
        location = self._location(partner=shop)
        combination = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([location.id])]
        })
        booking = self.env["resource.booking"].new({
            "type_id": self.rbt.id,
            "combination_id": combination.id,
            "combination_auto_assign": False,
        })
        work_start = self._next_monday()
        before_hours = booking._get_intervals(
            UTC.localize(work_start - timedelta(hours=1)),
            UTC.localize(work_start),
            combination,
        )
        self.assertFalse(before_hours)
        self.env["resource.calendar.leaves"].create({
            "name": "Shop closure",
            "date_from": work_start,
            "date_to": work_start + timedelta(hours=2),
            "calendar_id": self.r_calendars[2].id,
            "resource_id": location.id,
        })
        during_leave = booking._get_intervals(
            UTC.localize(work_start),
            UTC.localize(work_start + timedelta(hours=1)),
            combination,
        )
        self.assertFalse(during_leave)

    def test_locationless_busy_interval_delegates_to_ocapath(self):
        # A real overlapping appointment verifies the delegated busy interval.
        resource = self.r_materials[0]
        calendar = self.env["resource.calendar"]
        start = self._next_monday()
        booking = self.env["resource.booking"].create({
            "partner_ids": [Command.link(self.partner.id)],
            "type_id": self.rbt.id,
            "combination_id": self.rbcs[0].id,
            "combination_auto_assign": False,
            "start": fields.Datetime.to_string(start),
            "duration": 1,
        })
        self.assertTrue(booking.meeting_id)
        start = UTC.localize(start)
        stop = start + timedelta(hours=1)
        expected = OcaResourceCalendar._calendar_event_busy_intervals(
            calendar, start, stop, resource, -1
        )
        actual = calendar._calendar_event_busy_intervals(start, stop, resource, -1)
        self.assertTrue(expected._items)
        self.assertEqual(actual._items, expected._items)


class TestCombinationAvailabilityCalendar(ResourceBookingLocationCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        create_test_data(cls)

    def test_restricts_worker_days_per_shop(self):
        shops = self._location("Monday shop") | self._location("Tuesday shop")
        worker = self.r_users[2]
        combinations = self.env["resource.booking.combination"].create([
            {
                "resource_ids": [Command.set([shop.id, worker.id])],
                "availability_calendar_id": self.r_calendars[index].id,
            }
            for index, shop in enumerate(shops)
        ])
        booking = self.env["resource.booking"].new({"type_id": self.rbt.id})
        monday = UTC.localize(datetime(2099, 1, 5, 8))
        tuesday = monday + timedelta(days=1)

        self.assertTrue(booking._get_intervals(
            monday, monday + timedelta(hours=1), combinations[0]
        ))
        self.assertFalse(booking._get_intervals(
            tuesday, tuesday + timedelta(hours=1), combinations[0]
        ))
        self.assertFalse(booking._get_intervals(
            monday, monday + timedelta(hours=1), combinations[1]
        ))
        self.assertTrue(booking._get_intervals(
            tuesday, tuesday + timedelta(hours=1), combinations[1]
        ))

    def test_calendar_change_cannot_invalidate_confirmed_booking(self):
        availability = self.env["resource.calendar"].create({
            "name": "Monday shop assignment",
            "tz": "UTC",
            "attendance_ids": [Command.create({
                "name": "Monday", "dayofweek": "0", "hour_from": 8,
                "hour_to": 18, "day_period": "morning",
            })],
        })
        combination = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([
                self._location("Scheduled shop").id, self.r_users[2].id
            ])],
            "availability_calendar_id": availability.id,
        })
        booking = self.env["resource.booking"].create({
            "partner_ids": [Command.link(self.partner.id)],
            "type_id": self.rbt.id,
            "combination_id": combination.id,
            "combination_auto_assign": False,
            "start": "2099-01-05 08:00:00",
            "duration": 1,
        })
        booking.action_confirm()

        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            availability.write({
                "attendance_ids": [Command.update(
                    availability.attendance_ids.id, {"hour_from": 10}
                )]
            })
