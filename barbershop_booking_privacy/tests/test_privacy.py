from datetime import datetime

from freezegun import freeze_time
from pytz import UTC

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase

from odoo.addons.resource.models.utils import Intervals

from odoo.addons.resource_booking.tests.common import create_test_data


@freeze_time("2021-02-26 09:00:00", tick=True)
@tagged("post_install", "-at_install")
class TestBookingPrivacy(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        create_test_data(cls)
        cls.BarberGroup = cls.env.ref("barbershop_booking_privacy.group_barber")
        cls.ManagerGroup = cls.env.ref("barbershop_booking_privacy.group_manager")
        cls.barbers = cls.env["res.users"].create([
            {"name": f"Privacy barber {n}", "login": f"privacy_barber_{n}"}
            for n in range(3)
        ])
        cls.barbers.write({"groups_id": [Command.link(cls.BarberGroup.id)]})
        cls.unassigned = cls.barbers[2]
        cls.manager = new_test_user(
            cls.env,
            login="privacy_manager",
            groups="base.group_user,barbershop_booking_privacy.group_manager",
        )
        cls.portal = new_test_user(
            cls.env, login="privacy_portal", groups="base.group_portal"
        )
        cls.resources = cls.env["resource.resource"].create([
            {
                "name": barber.name,
                "resource_type": "user",
                "user_id": barber.id,
                "calendar_id": cls.r_calendars[2].id,
                "tz": "UTC",
            }
            for barber in cls.barbers
        ])
        # Both assigned combinations share one exclusive material resource.
        cls.shared_material = cls.r_materials[2]
        cls.combinations = cls.env["resource.booking.combination"].create([
            {"resource_ids": [Command.set([resource.id, cls.shared_material.id])]}
            for resource in cls.resources
        ])
        cls.rbt.combination_rel_ids = [
            Command.create({"combination_id": combination.id})
            for combination in cls.combinations
        ]
        Partner = cls.env["res.partner"]
        cls.private_a = Partner.create({
            "name": "Private A", "barbershop_private_booking_customer": True,
        })
        cls.private_b = Partner.create({
            "name": "Private B", "barbershop_private_booking_customer": True,
        })
        cls.private_shared = Partner.create({
            "name": "Shared private customer", "barbershop_private_booking_customer": True,
        })
        cls.ordinary = Partner.create({"name": "Ordinary customer"})
        Booking = cls.env["resource.booking"]
        cls.bookings = Booking.create([
            {
                "name": f"Privacy booking {i}",
                "type_id": cls.rbt.id,
                "combination_id": cls.combinations[i].id,
                "combination_auto_assign": False,
                "partner_ids": [Command.set((
                    (cls.private_a | cls.private_shared) if i == 0 else
                    (cls.private_b | cls.private_shared) if i == 1 else
                    cls.ordinary
                ).ids)],
                "start": [
                    "2021-03-01 10:00:00",
                    "2021-03-02 10:00:00",
                    "2021-03-08 10:00:00",
                ][i],
            }
            for i in range(3)
        ])
        # Add a pending combination with no user resource: it is manager-only.
        cls.no_user_combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set(cls.shared_material.ids)],
        })
        cls.rbt.combination_rel_ids = [
            Command.create({"combination_id": cls.no_user_combination.id})
        ]
        cls.no_user_booking = Booking.create({
            "name": "No user resource",
            "type_id": cls.rbt.id,
            "combination_id": cls.no_user_combination.id,
            "combination_auto_assign": False,
            "partner_ids": [Command.set(cls.ordinary.ids)],
        })
        cls.personal_event = cls.env["calendar.event"].create({
            "name": "Unrelated personal event",
            "start": "2021-03-01 10:00:00",
            "stop": "2021-03-01 11:00:00",
            "user_id": cls.unassigned.id,
            "partner_ids": [Command.set(cls.unassigned.partner_id.ids)],
        })
        cls.portal_booking = Booking.create({
            "name": "Portal own booking",
            "type_id": cls.rbt.id,
            "partner_ids": [Command.set(cls.portal.partner_id.ids)],
        })

    def _assert_hidden(self, model, record):
        user_model = self.env[model].with_user(self.barbers[0])
        self.assertFalse(user_model.search_count([("id", "=", record.id)]))
        self.assertNotIn(record.id, user_model.search([]).ids)
        with self.assertRaises(AccessError):
            user_model.browse(record.id).read()
        self.assertFalse(user_model.read_group([("id", "=", record.id)], ["id"], ["id"]))

    def test_booking_visibility_all_read_apis_and_manager_override(self):
        own, coworker, unassigned = self.bookings
        barber_bookings = self.env["resource.booking"].with_user(self.barbers[0])
        self.assertIn(own.id, barber_bookings.search([]).ids)
        self.assertTrue(barber_bookings.search_count([("id", "=", own.id)]))
        self.assertEqual(barber_bookings.browse(own.id).read(["name"])[0]["name"], own.name)
        self.assertTrue(barber_bookings.read_group([], ["state"], ["state"]))
        self._assert_hidden("resource.booking", coworker)
        self._assert_hidden("resource.booking", unassigned)
        self._assert_hidden("resource.booking", self.no_user_booking)
        manager_bookings = self.env["resource.booking"].with_user(self.manager)
        self.assertTrue(set(self.bookings.ids + self.no_user_booking.ids).issubset(
            set(manager_bookings.search([]).ids)
        ))
        self.assertTrue(set(self.bookings.mapped("meeting_id").ids).issubset(
            set(self.env["calendar.event"].with_user(self.manager).search([]).ids)
        ))

    def test_requester_follower_and_attendee_do_not_grant_access(self):
        coworker = self.bookings[1]
        coworker.write({"partner_ids": [Command.link(self.barbers[0].partner_id.id)]})
        coworker.message_subscribe(partner_ids=[self.barbers[0].partner_id.id])
        coworker.meeting_id.write({"partner_ids": [Command.link(self.barbers[0].partner_id.id)]})
        self._assert_hidden("resource.booking", coworker)
        self._assert_hidden("calendar.event", coworker.meeting_id)

    def test_event_visibility_personal_events_and_read_only(self):
        own_event = self.bookings[0].meeting_id
        coworker_event = self.bookings[1].meeting_id
        events = self.env["calendar.event"].with_user(self.barbers[0])
        self.assertIn(own_event.id, events.search([]).ids)
        self.assertTrue(events.search_count([("id", "=", own_event.id)]))
        self.assertEqual(events.browse(own_event.id).read(["name"])[0]["name"], own_event.name)
        self.assertTrue(events.read_group([], ["user_id"], ["user_id"]))
        self._assert_hidden("calendar.event", coworker_event)
        self.assertTrue(events.search_count([("id", "=", self.personal_event.id)]))
        personal = events.browse(self.personal_event.id)
        personal.write({"name": "Personal event remains editable"})
        personal.unlink()
        for vals in (
            {"name": "changed"},
            {"start": "2021-03-01 11:00:00", "stop": "2021-03-01 12:00:00"},
            {"partner_ids": [Command.clear()]},
        ):
            with self.assertRaises(AccessError):
                own_event.with_user(self.barbers[0]).write(vals)
        with self.assertRaises(AccessError):
            own_event.with_user(self.barbers[0]).unlink()
        with self.assertRaises(AccessError):
            events.create({"name": "Link attack", "resource_booking_ids": [Command.set(own_event.resource_booking_ids.ids)]})

    def test_booking_customer_private_visibility_and_edit_rights(self):
        Partner = self.env["res.partner"].with_user(self.barbers[0])
        self.assertTrue(Partner.search_count([("id", "=", self.private_a.id)]))
        self.assertEqual(Partner.browse(self.private_a.id).read(["name"])[0]["name"], "Private A")
        self.assertTrue(Partner.read_group([("id", "=", self.private_a.id)], ["company_id"], ["company_id"]))
        self.assertEqual(Partner.browse(self.private_shared.id).name, "Shared private customer")
        self._assert_hidden("res.partner", self.private_b)
        self._assert_hidden("res.partner", self.ordinary)
        self._assert_hidden("res.partner", self.barbers[1].partner_id)
        self.assertEqual(Partner.browse(self.barbers[0].partner_id.id).name, self.barbers[0].name)
        self.assertEqual(
            Partner.browse(self.barbers[0].company_id.partner_id.id).name,
            self.barbers[0].company_id.name,
        )
        with self.assertRaises(AccessError):
            Partner.browse(self.private_a.id).write({"name": "Private changed"})
        with self.assertRaises(AccessError):
            Partner.browse(self.private_a.id).write({"barbershop_private_booking_customer": False})
        self.assertEqual(self.env["res.partner"].with_user(self.barbers[1]).browse(self.private_shared.id).name,
                         "Shared private customer")
        third = self.env["res.partner"].with_user(self.barbers[2])
        self.assertFalse(third.search_count([("id", "=", self.private_shared.id)]))
        self.assertEqual(third.browse(self.ordinary.id).name, "Ordinary customer")
        third.browse(self.ordinary.id).write({"name": "Ordinary edited"})
        manager = self.env["res.partner"].with_user(self.manager)
        self.assertEqual(
            manager.search_count([("id", "in", (self.private_b | self.ordinary).ids)]),
            2,
        )

    def test_barber_booking_write_create_unlink_and_manager_crud(self):
        booking = self.bookings[0].with_user(self.barbers[0])
        for vals in (
            {"name": "changed"},
            {"partner_ids": [Command.clear()]},
            {"combination_id": self.combinations[1].id},
            {"start": "2021-03-01 12:00:00"},
        ):
            with self.assertRaises(AccessError):
                booking.write(vals)
        with self.assertRaises(AccessError):
            self.env["resource.booking"].with_user(self.barbers[0]).create({
                "type_id": self.rbt.id,
                "partner_ids": [Command.set(self.private_a.ids)],
            })
        with self.assertRaises(AccessError):
            booking.unlink()
        manager = self.env["resource.booking"].with_user(self.manager)
        new_booking = manager.create({
            "name": "Manager CRUD",
            "type_id": self.rbt.id,
            "combination_id": self.no_user_combination.id,
            "combination_auto_assign": False,
            "partner_ids": [Command.set(self.ordinary.ids)],
        })
        new_booking.write({"name": "Manager changed"})
        new_booking.unlink()
        manager_partner = self.env["res.partner"].with_user(self.manager)
        manager_partner.browse(self.ordinary.id).write({"barbershop_private_booking_customer": True})
        self.assertTrue(self.env["res.partner"].browse(self.ordinary.id).barbershop_private_booking_customer)
        self.env["calendar.event"].with_user(self.manager).create({
            "name": "Manager event", "start": "2021-03-01 12:00:00", "stop": "2021-03-01 13:00:00",
        }).unlink()

    def test_barber_cannot_mutate_booking_actions(self):
        booking = self.bookings[0].with_user(self.barbers[0])
        for action in ("action_confirm", "action_unschedule", "action_cancel"):
            with self.subTest(action=action), self.assertRaises(AccessError):
                getattr(booking, action)()

    def test_mixed_booking_event_is_hidden_from_each_assigned_barber(self):
        own, coworker = self.bookings[:2]
        event = own.meeting_id
        # Temporarily mimic legacy data predating the OCA one-to-one SQL index.
        # Rolling back this savepoint restores both the unique index and data.
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute("ALTER TABLE resource_booking DROP CONSTRAINT resource_booking_unique_meeting_id")
                self.env.cr.execute(
                    "UPDATE resource_booking SET meeting_id = %s WHERE id = %s",
                    [event.id, coworker.id],
                )
                self.env["resource.booking"].invalidate_model(["meeting_id"])
                for barber in self.barbers[:2]:
                    events = self.env["calendar.event"].with_user(barber)
                    self.assertFalse(events.search_count([("id", "=", event.id)]))
                    with self.assertRaises(AccessError):
                        events.browse(event.id).read(["name"])
                raise RuntimeError("rollback legacy mixed-event fixture")
        except RuntimeError as error:
            self.assertEqual(str(error), "rollback legacy mixed-event fixture")

    def test_portal_token_and_busy_hidden_event(self):
        portal_booking = self.portal_booking.with_user(self.portal)
        self.assertIn(self.portal_booking.id, self.env["resource.booking"].with_user(self.portal).search([]).ids)
        self.assertEqual(portal_booking.access_token, self.portal_booking.access_token)
        self.assertIn("access_token=", portal_booking.get_portal_url())
        # This exact coworker event is hidden by the event rule, but must still
        # occupy the shared exclusive material resource in availability checks.
        event = self.bookings[1].meeting_id
        self.assertFalse(self.env["calendar.event"].with_user(self.barbers[0]).search_count([
            ("id", "=", event.id),
        ]))
        start = UTC.localize(datetime(2021, 3, 2, 10))
        intervals = self.env["resource.calendar"].with_user(self.barbers[0])._calendar_event_busy_intervals(
            start,
            start.replace(hour=11),
            self.shared_material,
            -1,
        )
        self.assertIsInstance(intervals, Intervals)
        self.assertTrue(intervals, "Hidden booking event must still block its shared resource")

    def test_chatter_followers_and_attachments_are_not_exposed(self):
        booking = self.bookings[1]
        message = self.env["mail.message"].create({
            "model": "resource.booking",
            "res_id": booking.id,
            "body": "Coworker secret",
            "message_type": "comment",
            "subtype_id": self.env.ref("mail.mt_note").id,
        })
        follower = booking.message_follower_ids[:1]
        attachment = self.env["ir.attachment"].create({
            "name": "coworker-secret.txt",
            "type": "binary",
            "datas": "c2VjcmV0",
            "res_model": "resource.booking",
            "res_id": booking.id,
        })
        messages = self.env["mail.message"].with_user(self.barbers[0])
        self.assertFalse(messages.search_count([("id", "=", message.id)]))
        with self.assertRaises(AccessError):
            messages.browse(message.id).read(["body"])
        followers = self.env["mail.followers"].with_user(self.barbers[0])
        self.assertFalse(followers.search_count([("id", "=", follower.id)]))
        self.assertFalse(followers.browse(follower.id).read(["partner_id"]))
        attachments = self.env["ir.attachment"].with_user(self.barbers[0])
        self.assertFalse(attachments.search_count([("id", "=", attachment.id)]))
        with self.assertRaises(AccessError):
            attachments.browse(attachment.id).read(["datas"])
