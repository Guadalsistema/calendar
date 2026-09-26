# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from freezegun import freeze_time
from lxml.html import fromstring

from odoo.tests import tagged
from odoo.tests.common import HttpCase

from .common import create_test_data


@freeze_time("2021-02-26 09:00:00", tick=True)
@tagged("post_install", "-at_install")
class PortalCombinationCase(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        create_test_data(cls)

    def _url_xml(self, url, data=None):
        return fromstring(self.url_open(url, data).content)

    def _new_booking(self, partner=None):
        return self.env["resource.booking"].create(
            {
                "partner_ids": [(4, (partner or self.partner).id)],
                "type_id": self.rbt.id,
            }
        )

    def test_portal_choose_combination(self):
        booking = self._new_booking()
        monday, tuesday = self.rbcs[:2]
        monday_page = self._url_xml(
            booking.get_portal_url(
                suffix="/schedule/2021/3",
                query_string=f"&combination_id={monday.id}",
            )
        )
        self.assertTrue(monday_page.cssselect("#dropdown-trigger-2021-03-01"))
        self.assertFalse(monday_page.cssselect("#dropdown-trigger-2021-03-02"))
        tuesday_page = self._url_xml(
            booking.get_portal_url(
                suffix="/schedule/2021/3",
                query_string=f"&combination_id={tuesday.id}",
            )
        )
        self.assertFalse(tuesday_page.cssselect("#dropdown-trigger-2021-03-01"))
        self.assertTrue(tuesday_page.cssselect("#dropdown-trigger-2021-03-02"))
        next_month = tuesday_page.cssselect('a[title="Next month"]')[0]
        self.assertIn(f"combination_id={tuesday.id}", next_month.get("href"))
        form = tuesday_page.cssselect("form.modal")[0]
        self.assertEqual(
            form.cssselect('input[name="combination_id"]')[0].get("value"),
            str(tuesday.id),
        )
        data = {
            element.get("name"): element.get("value")
            for element in form.cssselect("input")
        }
        confirmation = self._url_xml(form.get("action"), data)
        self.assertTrue(confirmation.cssselect('.badge:contains("Confirmed")'))
        booking.invalidate_recordset()
        self.assertEqual(booking.combination_id, tuesday)
        self.assertFalse(booking.combination_auto_assign)

    def test_portal_reject_unconfigured_or_fixed_combination(self):
        booking = self._new_booking()
        foreign = self.env["resource.booking.combination"].create(
            {"resource_ids": [(6, 0, self.r_users[:1].ids)]}
        )
        invalid_url = booking.get_portal_url(
            suffix="/schedule/2021/3",
            query_string=f"&combination_id={foreign.id}",
        )
        self.assertEqual(self.url_open(invalid_url).status_code, 404)
        booking.write(
            {"combination_auto_assign": False, "combination_id": self.rbcs[0].id}
        )
        fixed_url = booking.get_portal_url(
            suffix="/schedule/2021/3",
            query_string=f"&combination_id={self.rbcs[1].id}",
        )
        self.assertEqual(self.url_open(fixed_url).status_code, 404)
        fixed_page = self._url_xml(booking.get_portal_url(suffix="/schedule/2021/3"))
        self.assertFalse(
            fixed_page.cssselect("a:contains('Any available combination')")
        )
        form = fixed_page.cssselect("form.modal")[0]
        data = {
            element.get("name"): element.get("value")
            for element in form.cssselect("input")
        }
        data["combination_id"] = str(self.rbcs[1].id)
        response = self._url_xml(form.get("action"), data)
        self.assertTrue(response.cssselect(".alert-danger"))
        booking.invalidate_recordset()
        self.assertEqual(booking.state, "pending")

    def test_reschedule_auto_assignment_can_choose_another_combination(self):
        self.rbt.combination_assignment = "sorted"
        booking = self._new_booking()
        booking.start = "2021-03-01 10:00:00"
        booking.action_confirm()
        self.assertEqual(booking.combination_id, self.rbcs[0])
        portal_page = self._url_xml(booking.get_portal_url())
        self.assertTrue(portal_page.cssselect("a:contains('Reschedule')"))
        page = self._url_xml(
            booking.get_portal_url(
                suffix="/schedule/2021/3",
                query_string=f"&combination_id={self.rbcs[1].id}",
            )
        )
        self.assertTrue(page.cssselect("#dropdown-trigger-2021-03-02"))
        form = page.cssselect("form.modal")[0]
        data = {
            element.get("name"): element.get("value")
            for element in form.cssselect("input")
        }
        self._url_xml(form.get("action"), data)
        booking.invalidate_recordset()
        self.assertEqual(booking.combination_id, self.rbcs[1])
        self.assertFalse(booking.combination_auto_assign)

    def test_portal_choice_requires_booking_access(self):
        booking = self._new_booking()
        url = (
            f"/my/bookings/{booking.id}/schedule/2021/3"
            f"?combination_id={self.rbcs[0].id}"
        )
        page = self._url_xml(url)
        self.assertFalse(page.cssselect("#dropdown-trigger-2021-03-01"))
