from freezegun import freeze_time

from odoo import Command
from odoo.tests import new_test_user, tagged
from odoo.tests.common import HttpCase

from odoo.addons.resource_booking.tests.common import create_test_data


@freeze_time("2021-02-26 09:00:00", tick=True)
@tagged("post_install", "-at_install")
class TestBookingPrivacyPortal(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        create_test_data(cls)
        cls.barber = new_test_user(
            cls.env,
            login="portal_barber",
            password="portal_barber",
            groups="barbershop_booking_privacy.group_barber",
        )
        cls.manager = new_test_user(
            cls.env,
            login="portal_manager",
            password="portal_manager",
            groups="barbershop_booking_privacy.group_manager",
        )
        cls.portal_customer = new_test_user(
            cls.env,
            login="portal_customer",
            password="portal_customer",
            groups="base.group_portal",
        )
        cls.other_portal_customer = new_test_user(
            cls.env,
            login="other_portal_customer",
            password="other_portal_customer",
            groups="base.group_portal",
        )
        resource = cls.env["resource.resource"].create({
            "name": "Portal barber resource",
            "resource_type": "user",
            "user_id": cls.barber.id,
            "calendar_id": cls.r_calendars[2].id,
            "tz": "UTC",
        })
        combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set(resource.ids)],
        })
        cls.rbt.combination_rel_ids = [
            Command.create({"combination_id": combination.id})
        ]
        cls.booking = cls.env["resource.booking"].create({
            "name": "Portal protected booking",
            "type_id": cls.rbt.id,
            "combination_id": combination.id,
            "combination_auto_assign": False,
            "partner_ids": [Command.set(cls.portal_customer.partner_id.ids)],
            "start": "2021-03-01 10:00:00",
        })
        cls.booking.action_confirm()
        cls.other_booking = cls.env["resource.booking"].create({
            "name": "Other customer private booking",
            "type_id": cls.rbt.id,
            "combination_id": combination.id,
            "combination_auto_assign": False,
            "partner_ids": [Command.set(cls.other_portal_customer.partner_id.ids)],
            "start": "2021-03-01 12:00:00",
        })
        cls.other_booking.action_confirm()

    def test_portal_customer_cannot_access_another_customer_booking(self):
        self.authenticate("portal_customer", "portal_customer")
        own_page = self.url_open(f"/my/bookings/{self.booking.id}")
        self.assertEqual(own_page.status_code, 200)
        self.assertIn("Portal protected booking", own_page.text)

        other_page = self.url_open(
            f"/my/bookings/{self.other_booking.id}", allow_redirects=False
        )
        self.assertEqual(other_page.status_code, 303)
        self.assertEqual(other_page.headers["Location"], "/my")

    def test_barber_cannot_cancel_or_reschedule_from_portal(self):
        self.authenticate("portal_barber", "portal_barber")
        page = self.url_open(self.booking.get_portal_url())
        self.assertNotIn("Cancel this booking", page.text)
        self.assertNotIn("Reschedule", page.text)

        cancel = self.url_open(self.booking.get_portal_url(suffix="/cancel"))
        self.assertEqual(cancel.status_code, 403)
        confirm_url = self.booking.get_portal_url(suffix="/confirm")
        confirm = self.url_open(
            f"{confirm_url}&when=2021-03-01T11%3A00%3A00%2B00%3A00"
        )
        self.assertEqual(confirm.status_code, 403)
        self.booking.invalidate_recordset(["state", "start"])
        self.assertEqual(self.booking.state, "confirmed")
        self.assertEqual(str(self.booking.start), "2021-03-01 10:00:00")

        self.authenticate("portal_manager", "portal_manager")
        manager_page = self.url_open(self.booking.get_portal_url())
        self.assertIn("Cancel this booking", manager_page.text)
        self.url_open(self.booking.get_portal_url(suffix="/cancel"))
        self.booking.invalidate_recordset(["state"])
        self.assertEqual(self.booking.state, "canceled")
