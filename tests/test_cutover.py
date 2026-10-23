"""Unit tests for the cutover planner. Run with `python -m pytest -q` (or `python -m unittest`)."""
from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from cutover import checks, cli, report, simulate
from cutover.model import (Data, Device, Settings, User, Wave, build_number, load, parse_date, settings_from_dict, yes)

AS_OF = date(2026, 10, 23)


def good_device(**kw) -> Device:
    base = dict(name="PC-1", user="ana@x.com", site="Madrid", os_build=22631, tpm=True, secure_boot=True, ram_gb=16, disk_free_gb=100,
                join_type="hybrid", management="comanaged", autopilot=True, bitlocker_key=True, compliant=True, last_seen=AS_OF)
    base.update(kw)
    return Device(**base)


def good_user(**kw) -> User:
    base = dict(upn="ana@x.com", department="Finance", site="Madrid", mailbox_gb=5, mfa=True, last_signin=AS_OF, licences=["E3"])
    base.update(kw)
    return User(**base)


def settings(**kw) -> Settings:
    s = Settings()
    s.waves = [Wave("W1", date(2026, 11, 16), sites=["Madrid"], days=1, techs=2)]
    s.licence_map = {"E3": "E3"}
    s.prerequisites = {"tenant": True}
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def data(devices=None, users=None, s: Settings | None = None) -> Data:
    d = Data(devices if devices is not None else [good_device()], users if users is not None else [good_user()], s or settings(), AS_OF)
    checks.assign_waves(d)
    return d


class Parsing(unittest.TestCase):
    def test_yes_no_and_dates(self):
        self.assertTrue(yes("Yes")); self.assertTrue(yes("true")); self.assertTrue(yes("1"))
        self.assertFalse(yes("No")); self.assertFalse(yes("")); self.assertFalse(yes("n/a"))
        self.assertEqual(parse_date("2026-10-23T08:15:00Z"), AS_OF)
        self.assertEqual(parse_date("23/10/2026"), AS_OF)
        self.assertIsNone(parse_date("never"))

    def test_build_numbers(self):
        self.assertEqual(build_number("10.0.22631.4317"), 22631)
        self.assertEqual(build_number("23H2"), 22631)
        self.assertEqual(build_number("10.0.19045.5011"), 19045)
        self.assertEqual(build_number("Windows 11 24H2"), 26100)
        self.assertEqual(build_number(""), 0)

    def test_csv_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "devices.csv").write_text("Device name,Primary user UPN,Site,OS version,TPM,Secure Boot,RAM (GB),Join type,Management agent,"
                                            "Autopilot registered,BitLocker key escrowed,Last sync\n"
                                            "PC-1,ana@x.com,Madrid,10.0.22631.4317,Yes,Yes,16,Hybrid Azure AD joined,Co-managed,Yes,Yes,2026-10-22\n",
                                            encoding="utf-8")
            (p / "users.csv").write_text("UserPrincipalName,Department,Office,Mailbox size (GB),MFA registered\nana@x.com,Finance,Madrid,12.5,TRUE\n",
                                         encoding="utf-8")
            d = load(p)
            self.assertEqual(d.devices[0].os_build, 22631)
            self.assertEqual(d.devices[0].join_type, "hybrid")
            self.assertEqual(d.devices[0].management, "comanaged")
            self.assertTrue(d.devices[0].autopilot and d.devices[0].bitlocker_key and d.devices[0].tpm)
            self.assertEqual(d.users[0].mailbox_gb, 12.5)
            self.assertTrue(d.users[0].mfa)
            self.assertEqual(d.as_of, date(2026, 10, 22))

    def test_missing_hardware_columns_assume_capable(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "devices.csv").write_text("Device name,Autopilot registered,BitLocker key escrowed,Last sync\nPC-1,Yes,Yes,2026-10-22\n", encoding="utf-8")
            d = load(p)
            self.assertTrue(d.devices[0].tpm and d.devices[0].secure_boot)
            self.assertEqual(checks.device_readiness(d)[0]["verdict"], checks.READY)

    def test_settings_from_dict(self):
        s = settings_from_dict({"waves": [{"name": "W1", "date": "2026-11-16", "sites": ["Madrid"], "techs": 3, "days": 2}],
                                "freeze": [{"from": "2026-12-23", "to": "2027-01-07"}], "app_catalog": {"7-Zip": "approved"},
                                "migration_gb_per_hour": "50"})
        self.assertEqual(s.waves[0].techs, 3)
        self.assertEqual(s.freeze[0], (date(2026, 12, 23), date(2027, 1, 7)))
        self.assertEqual(s.app_catalog["7-zip"], "approved")
        self.assertEqual(s.migration_gb_per_hour, 50.0)


class DeviceReadiness(unittest.TestCase):
    def verdict(self, dv: Device, users=None):
        rows = checks.device_readiness(data([dv], users))
        return rows[0]["verdict"], rows[0]["blockers"], rows[0]["actions"]

    def test_ready_device(self):
        self.assertEqual(self.verdict(good_device())[0], checks.READY)

    def test_windows11_hardware_blocks(self):
        v, blockers, _ = self.verdict(good_device(tpm=False, ram_gb=4))
        self.assertEqual(v, checks.BLOCKED)
        self.assertIn("no TPM 2.0", blockers[0]); self.assertIn("4 GB RAM", blockers[0])

    def test_old_build_is_an_action_not_a_blocker(self):
        v, blockers, actions = self.verdict(good_device(os_build=19045))
        self.assertEqual(v, checks.ACTIONS)
        self.assertFalse(blockers); self.assertIn("OS build 19045", actions[0])

    def test_autopilot_and_bitlocker_blockers(self):
        v, blockers, _ = self.verdict(good_device(autopilot=False, bitlocker_key=False))
        self.assertEqual(v, checks.BLOCKED)
        self.assertTrue(any("Autopilot" in b for b in blockers))
        self.assertTrue(any("BitLocker" in b for b in blockers))

    def test_stale_devices(self):
        _, _, actions = self.verdict(good_device(last_seen=date(2026, 10, 1)))
        self.assertTrue(any("switch on and sync" in a for a in actions))
        v, blockers, _ = self.verdict(good_device(last_seen=date(2026, 6, 1)))
        self.assertEqual(v, checks.BLOCKED); self.assertIn("probably retired", blockers[0])

    def test_leaver_excludes_device(self):
        v, blockers, _ = self.verdict(good_device(autopilot=False), users=[good_user(leaving=True)])
        self.assertEqual(v, checks.EXCLUDE); self.assertFalse(blockers)

    def test_unknown_apps_and_domain_join(self):
        s = settings(app_catalog={"7-zip": "approved", "oldtool": "blocked"})
        rows = checks.device_readiness(data([good_device(join_type="domain", management="mecm", apps=["7-Zip", "OldTool", "NewThing"])], s=s))
        actions = rows[0]["actions"]
        self.assertTrue(any("domain-joined" in a for a in actions))
        self.assertTrue(any("apps without a target decision: NewThing, OldTool" in a for a in actions))


class UserReadiness(unittest.TestCase):
    def test_mfa_mailbox_and_licence(self):
        s = settings(licence_map={"E3": "E3", "Visio Plan 2": ""})
        rows = checks.user_readiness(data(users=[good_user(mfa=False, mailbox_gb=80, licences=["E3", "Visio Plan 2"])], s=s))
        b = " | ".join(rows[0]["blockers"])
        self.assertEqual(rows[0]["verdict"], checks.BLOCKED)
        self.assertIn("no MFA", b); self.assertIn("mailbox 80 GB over 50", b); self.assertIn("Visio Plan 2", b)

    def test_shared_mailbox_split_across_waves(self):
        s = settings()
        s.waves = [Wave("W1", date(2026, 11, 16), sites=["Madrid"]), Wave("W2", date(2026, 11, 23), sites=["Lisbon"])]
        users = [good_user(upn="a@x.com", shared_mailboxes=["ap@x.com"]), good_user(upn="b@x.com", site="Lisbon", shared_mailboxes=["ap@x.com"])]
        rows = checks.user_readiness(data([], users, s))
        self.assertTrue(all(any("shared mailbox with users in several waves: ap@x.com" in a for a in r["actions"]) for r in rows))

    def test_dormant_and_leaver(self):
        rows = checks.user_readiness(data(users=[good_user(last_signin=date(2026, 5, 1)), good_user(upn="x@x.com", leaving=True)]))
        self.assertTrue(any("no sign-in for" in a for a in rows[0]["actions"]))
        self.assertEqual(rows[1]["verdict"], checks.EXCLUDE)


class Waves(unittest.TestCase):
    def test_assignment_rules(self):
        s = settings()
        s.waves = [Wave("Pilot", date(2026, 11, 9), sites=["Madrid"], departments=["IT"], pilot=True),
                   Wave("W1", date(2026, 11, 16), sites=["Madrid"])]
        users = [good_user(upn="it@x.com", department="IT"), good_user(upn="fin@x.com"), good_user(upn="lis@x.com", site="Lisbon")]
        devices = [good_device(name="K-1", user="", kind="kiosk"), good_device(name="D-1", user="it@x.com")]
        d = data(devices, users, s)
        self.assertEqual([u.wave for u in d.users], ["Pilot", "W1", ""])
        self.assertEqual(d.devices[0].wave, "W1", "a device without a user never lands in a department wave")
        self.assertEqual(d.devices[1].wave, "Pilot")
        dev_un, usr_un = checks.unassigned(d)
        self.assertEqual([r["upn"] for r in usr_un], ["lis@x.com"])

    def test_capacity_and_days_needed(self):
        s = settings(devices_per_tech_day=10)
        s.waves = [Wave("W1", date(2026, 11, 16), sites=["Madrid"], days=1, techs=2)]
        devices = [good_device(name=f"PC-{i}", user="") for i in range(25)]
        plan = checks.wave_plan(data(devices, [], s))[0]
        self.assertEqual(plan["capacity"], 20)
        self.assertEqual(plan["days_needed"], 2)
        self.assertEqual(plan["verdict"], "no-go")
        self.assertIn("25 devices for a capacity of 20", plan["reasons"][0])

    def test_freeze_blocked_share_and_mail_window(self):
        s = settings(freeze=[(date(2026, 12, 23), date(2027, 1, 7))], migration_gb_per_hour=10, window_hours=5)
        s.waves = [Wave("W5", date(2026, 12, 28), sites=["Madrid"], days=2, techs=4)]
        devices = [good_device(name=f"PC-{i}", user=f"u{i}@x.com", autopilot=i < 2) for i in range(10)]
        users = [good_user(upn=f"u{i}@x.com", mailbox_gb=10) for i in range(10)]
        plan = checks.wave_plan(data(devices, users, s))[0]
        self.assertEqual(plan["verdict"], "no-go")
        text = " ; ".join(plan["reasons"])
        self.assertIn("freeze 23 Dec–07 Jan", text)
        self.assertIn("80% of devices blocked", text)
        self.assertIn("100 GB of mailboxes need 10 h", text)

    def test_pilot_needs_prerequisites_and_go_with_actions(self):
        s = settings(prerequisites={"a": True, "b": False})
        s.waves = [Wave("Pilot", date(2026, 11, 9), sites=["Madrid"], pilot=True), Wave("W1", date(2026, 11, 16), sites=["Lisbon"])]
        devices = [good_device(name="P-1"), good_device(name="L-1", user="b@x.com", site="Lisbon")]
        users = [good_user(), good_user(upn="b@x.com", site="Lisbon", mfa=False)]
        plan = checks.wave_plan(data(devices, users, s))
        self.assertEqual(plan[0]["verdict"], "no-go"); self.assertIn("prerequisites", plan[0]["reasons"][0])
        self.assertEqual(plan[1]["verdict"], "go with actions"); self.assertEqual(plan[1]["blocked_users"], 1)
        s.prerequisites["b"] = True
        self.assertEqual(checks.wave_plan(data(devices, users, s))[0]["verdict"], "go")


class Findings(unittest.TestCase):
    def test_codes_and_area_status(self):
        s = settings(prerequisites={"a": False}, app_catalog={"7-zip": "approved"})
        devices = [good_device(name=f"PC-{i}", user="", autopilot=i > 11, apps=["Mystery"]) for i in range(15)]
        f = checks.run_all(data(devices, [], s))
        codes = {x.code for x in f}
        self.assertTrue({"DEV-01", "APP-01", "PRE-01"} <= codes)
        self.assertEqual(sum(x.code == "DEV-01" for x in f), 1, "one finding per blocker reason, not per device")
        self.assertEqual(next(x for x in f if x.code == "DEV-01").severity, checks.CRITICAL)
        self.assertEqual(checks.area_status(f)["Prerequisites"], checks.CRITICAL)
        self.assertEqual([x.severity for x in f], sorted((x.severity for x in f), key=[checks.CRITICAL, checks.WARNING, checks.INFO].index))


class EndToEnd(unittest.TestCase):
    def test_demo_is_deterministic_and_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b, out = Path(tmp) / "a", Path(tmp) / "b", Path(tmp) / "out"
            simulate.write(a); simulate.write(b)
            self.assertEqual((a / "devices.csv").read_bytes(), (b / "devices.csv").read_bytes())
            self.assertEqual((a / "users.csv").read_bytes(), (b / "users.csv").read_bytes())
            self.assertEqual(cli.main(["plan", str(a), "--out", str(out), "--title", "T"]), 0)
            names = {"report.html", "cutover-plan.md", "waves.csv", "device-readiness.csv", "user-readiness.csv", "apps.csv",
                     "blockers.csv", "actions.csv", "findings.csv"}
            self.assertEqual({p.name for p in out.iterdir()}, names)
            waves = list(csv.DictReader((out / "waves.csv").open(encoding="utf-8")))
            self.assertEqual(len(waves), 6)
            self.assertEqual({w["verdict"] for w in waves}, {"go with actions", "no-go"})
            self.assertIn("freeze", next(w for w in waves if w["wave"].startswith("W5"))["reasons"])
            html = (out / "report.html").read_text(encoding="utf-8")
            self.assertIn("Fix first", html); self.assertIn("W3 Milan", html)
            md = (out / "cutover-plan.md").read_text(encoding="utf-8")
            self.assertIn("## Cutover day, in order", md)
            self.assertIn("- [ ] Service desk briefed", md)

    def test_plan_on_missing_folder_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(cli.main(["plan", str(Path(tmp) / "nope"), "--out", str(Path(tmp) / "o")]), 2)


if __name__ == "__main__":
    unittest.main()
