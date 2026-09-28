import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timedelta

import pytz

from odoo import Command, api
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

_logger = logging.getLogger(__name__)


def _confirmation_process(dbname, config_path, db_settings, args, ready_path, go_path):
    """Fresh-interpreter worker; never inherits the Odoo server registry lock."""
    try:
        from odoo.tools import config

        config.parse_config(["-c", config_path, f"--db-filter=^{dbname}$"])
        for key, value in db_settings.items():
            config[key] = value
        from odoo.modules.registry import Registry
        from odoo import api
        registry = Registry.new(dbname)
        with registry.cursor() as cr:
            env = api.Environment(cr, 1, {})
            with open(ready_path, "w", encoding="utf-8") as ready:
                ready.write("ready")
            deadline = time.monotonic() + 20
            while not os.path.exists(go_path):
                if time.monotonic() > deadline:
                    raise TimeoutError("parent did not release confirmation barrier")
                time.sleep(0.01)
            try:
                with cr.savepoint():
                    booking = env["barbershop.booking.choice"].confirm_booking(
                        args["company"], args["type"], args["mode"],
                        args["location"], args["combination"],
                        datetime.fromisoformat(args["when"]),
                        args["contact"],
                    )
                cr.commit()
                result = ("success", booking.id, args["email"])
            except ValidationError as error:
                cr.rollback()
                result = ("unavailable", str(error), args["email"])
        print(json.dumps(result), flush=True)
    except Exception:
        print(json.dumps(("unexpected", traceback.format_exc(), args["email"])), flush=True)
        raise


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "_confirmation_process":
    _confirmation_process(*json.loads(sys.argv[2]))


@tagged("-at_install", "post_install")
class TestBookingChoiceConcurrency(TransactionCase):
    """Exercise real competing confirmations on independent PG transactions."""

    def _committed_fixture(self, kind):
        registry = self.env.registry
        with registry.cursor() as cr:
            env = api.Environment(cr, self.env.uid, {})
            company = env.company
            calendar = company.resource_calendar_id
            material = env["resource.resource"].create({
                "name": f"T3 shared material {kind}", "resource_type": "material",
                "calendar_id": calendar.id, "company_id": company.id,
            })
            other_materials = env["resource.resource"].create([
                {"name": f"T3 material {kind} {index}", "resource_type": "material",
                 "calendar_id": calendar.id, "company_id": company.id}
                for index in range(2)
            ])
            address = env["res.partner"].create({
                "name": f"T3 shop address {kind}", "type": "other",
                "street": "T3 test address", "company_id": company.id,
            })
            location = env["resource.resource"].create({
                "name": f"T3 shop {kind}", "resource_type": "location",
                "location_partner_id": address.id, "calendar_id": calendar.id,
                "company_id": company.id, "tz": calendar.tz,
            })
            combos = env["resource.booking.combination"].create([
                {"resource_ids": [Command.set([material.id, other_materials[0].id])]},
                {"resource_ids": [Command.set([material.id, other_materials[1].id])]},
            ])
            types = env["resource.booking.type"].create([
                {"name": f"T3 type {kind} {index}", "company_id": company.id,
                 "resource_calendar_id": calendar.id, "duration": 1,
                 "slot_duration": 0.5, "modifications_deadline": 0}
                for index in range(2)
            ])
            for booking_type, combo in zip(types, combos):
                booking_type.write({"combination_rel_ids": [
                    Command.create({"combination_id": combo.id})
                ]})
            # For capacity-unlimited location checks, each candidate contains
            # only the shared location and no exclusive resource.
            location_combos = env["resource.booking.combination"].create([
                {"resource_ids": [Command.set([location.id])]} for _ in range(2)
            ])
            located_combos = env["resource.booking.combination"].create([
                {"resource_ids": [Command.set([location.id, other_materials[index].id])]}
                for index in range(2)
            ])
            location_types = env["resource.booking.type"].create([
                {"name": f"T3 location-only {kind} {index}", "company_id": company.id,
                 "resource_calendar_id": calendar.id, "duration": 1,
                 "slot_duration": 0.5, "modifications_deadline": 0}
                for index in range(2)
            ])
            located_types = env["resource.booking.type"].create([
                {"name": f"T3 located {kind} {index}", "company_id": company.id,
                 "resource_calendar_id": calendar.id, "duration": 1,
                 "slot_duration": 0.5, "modifications_deadline": 0}
                for index in range(2)
            ])
            for booking_type, combo in zip(location_types, location_combos):
                booking_type.write({"combination_rel_ids": [
                    Command.create({"combination_id": combo.id})
                ]})
            for booking_type, combo in zip(located_types, located_combos):
                booking_type.write({"combination_rel_ids": [
                    Command.create({"combination_id": combo.id})
                ]})
            ids = {
                "company": company.id, "location": location.id,
                "address": address.id, "resources": (material | other_materials).ids,
                "combos": combos.ids, "types": types.ids,
                "location_combos": location_combos.ids,
                "location_types": location_types.ids,
                "located_combos": located_combos.ids,
                "located_types": located_types.ids,
            }
            cr.commit()
        return ids

    def _slot(self):
        day = datetime.now(pytz.UTC).date()
        day += timedelta(days=(7 - day.weekday()) % 7 or 7)
        return pytz.UTC.localize(datetime.combine(day, datetime.min.time()).replace(hour=10))

    def _race_confirmations(self, fixture, types, combos, mode="independent", location_id=None):
        when = self._slot()
        from odoo.tools import config

        outcomes = []
        with tempfile.TemporaryDirectory(prefix="barbershop-choice-race-") as tempdir:
            processes = []
            entries = []
            for index in range(len(types)):
                email = f"t3-race-{fixture['location']}-{types[index]}@example.test"
                args = {
                    "company": fixture["company"], "type": types[index],
                    "mode": mode, "location": location_id,
                    "combination": combos[index], "when": when.isoformat(),
                    "contact": {"name": f"Concurrent {index}", "email": email,
                                "phone": f"+1 555 000 {index:04d}"},
                    "email": email,
                }
                ready_path = os.path.join(tempdir, f"ready-{index}")
                go_path = os.path.join(tempdir, f"go-{index}")
                settings = {
                    key: config[key]
                    for key in ("db_host", "db_port", "db_user", "db_password", "db_sslmode", "addons_path")
                }
                payload = [self.env.cr.dbname, config.rcfile, settings, args, ready_path, go_path]
                process = subprocess.Popen(
                    [sys.executable, __file__, "_confirmation_process", json.dumps(payload)],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                )
                processes.append(process)
                entries.append((ready_path, go_path))
            deadline = time.monotonic() + 30
            while not all(os.path.exists(ready) for ready, _go in entries):
                if time.monotonic() > deadline:
                    for process in processes:
                        if process.poll() is None:
                            process.terminate()
                    diagnostics = [process.communicate()[0] for process in processes]
                    self.fail(f"child did not reach confirmation barrier: {diagnostics}")
                time.sleep(0.02)
            for _ready, go_path in entries:
                with open(go_path, "w", encoding="utf-8") as release:
                    release.write("go")
            for process in processes:
                try:
                    output, _ = process.communicate(timeout=45)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    output, _ = process.communicate(timeout=5)
                    self.fail(f"confirmation child timed out: {output}")
                self.assertEqual(process.returncode, 0, output)
                result_line = [line for line in output.splitlines() if line.startswith("[")][-1]
                outcome = tuple(json.loads(result_line))
                self.assertNotEqual(outcome[0], "unexpected", outcome[1])
                outcomes.append(outcome)
        return outcomes, when

    def _cleanup_fixture(self, fixture):
        with self.env.registry.cursor() as cr:
            env = api.Environment(cr, self.env.uid, {})
            bookings = env["resource.booking"].search([
                ("partner_ids.email", "like", "t3-race-%@example.test")
            ])
            bookings.unlink()
            env["res.partner"].search([
                ("email", "like", "t3-race-%@example.test")
            ]).unlink()
            env["resource.booking.type"].browse(
                fixture["types"] + fixture["location_types"]
                + fixture["located_types"]
            ).unlink()
            env["resource.booking.combination"].browse(
                fixture["combos"] + fixture["location_combos"]
                + fixture["located_combos"]
            ).unlink()
            env["resource.resource"].browse(
                fixture["resources"] + [fixture["location"]]
            ).unlink()
            env["res.partner"].browse(fixture["address"]).unlink()
            cr.commit()

    def _assert_outcomes(self, fixture, outcomes, successes, failures, label):
        self.assertEqual(sum(row[0] == "success" for row in outcomes), successes, outcomes)
        self.assertEqual(sum(row[0] == "unavailable" for row in outcomes), failures, outcomes)
        _logger.info("T3 confirmation concurrency %s outcomes: %s", label, outcomes)
        with self.env.registry.cursor() as cr:
            env = api.Environment(cr, self.env.uid, {})
            emails = [row[2] for row in outcomes]
            bookings = env["resource.booking"].search([("partner_ids.email", "in", emails)])
            partners = env["res.partner"].search([("email", "in", emails)])
            self.assertEqual(len(bookings), successes)
            self.assertEqual(len(partners), successes)
            self.assertEqual(len(bookings.mapped("partner_ids")), successes)
            cr.rollback()

    def test_cross_type_shared_exclusive_resource_allows_one_confirmation(self):
        fixture = self._committed_fixture("exclusive")
        try:
            outcomes, _when = self._race_confirmations(
                fixture, fixture["types"], fixture["combos"]
            )
            self._assert_outcomes(fixture, outcomes, 1, 1, "shared-exclusive")
        finally:
            self._cleanup_fixture(fixture)

    def test_shared_location_distinct_exclusive_resources_both_confirm(self):
        fixture = self._committed_fixture("shop-shared")
        try:
            outcomes, _when = self._race_confirmations(
                fixture, fixture["located_types"], fixture["located_combos"],
                "location", fixture["location"],
            )
            self._assert_outcomes(fixture, outcomes, 2, 0, "shared-location-distinct-exclusive")
            outcomes, _when = self._race_confirmations(
                fixture, fixture["location_types"], fixture["location_combos"],
                "location", fixture["location"],
            )
            self._assert_outcomes(fixture, outcomes, 2, 0, "location-only")
        finally:
            self._cleanup_fixture(fixture)
