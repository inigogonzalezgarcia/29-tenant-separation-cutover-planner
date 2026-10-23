"""The checks: device readiness, user and mailbox readiness, applications, wave assignment and capacity, go/no-go.

Everything works on the Data object from model.py and returns plain rows (dicts) and Findings, so the report
module can render them and the tests can assert on them.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .model import Data, Device, Settings, User, Wave

CRITICAL, WARNING, INFO = "critical", "warning", "info"
AREAS = ("Devices", "Users & mailboxes", "Applications", "Waves & capacity", "Prerequisites")
READY, ACTIONS, BLOCKED, EXCLUDE = "ready", "ready with actions", "blocked", "exclude"


@dataclass
class Finding:
    severity: str
    area: str
    code: str
    subject: str
    detail: str
    fix: str
    owner: str = ""
    wave: str = ""
    items: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- waves

def assign_waves(d: Data) -> None:
    """Each user gets the first wave that matches: a wave with sites and departments needs both; with only one list, that one.
    Devices follow their primary user; devices without a user match the first site-only wave for their site."""
    s = d.settings
    for u in d.users:
        u.wave = ""
        for w in s.waves:
            if _matches(w, u.site, u.department):
                u.wave = w.name
                break
    for dv in d.devices:
        dv.wave = ""
        u = d.by_upn.get(dv.user)
        if u and u.wave:
            dv.wave = u.wave
            continue
        for w in s.waves:
            if dv.site and dv.site in w.sites and not w.departments:
                dv.wave = w.name
                break


def _matches(w: Wave, site: str, department: str) -> bool:
    ok_site = bool(site) and site in w.sites
    ok_dept = bool(department) and department in w.departments
    if w.sites and w.departments:
        return ok_site and ok_dept
    return ok_site or ok_dept


def wave_of(d: Data, name: str) -> Wave | None:
    return next((w for w in d.settings.waves if w.name == name), None)


def in_freeze(when: date, s: Settings, days: int = 1) -> tuple[date, date] | None:
    end = when + timedelta(days=max(days - 1, 0))
    for a, b in s.freeze:
        if when <= b and end >= a:
            return a, b
    return None


# ---------------------------------------------------------------- devices

def device_readiness(d: Data) -> list[dict]:
    """One row per device: blockers (must be fixed before the wave), actions (can be done in the wave) and a verdict."""
    s = d.settings
    rows = []
    for dv in d.devices:
        blockers, actions, notes = [], [], []
        age = (d.as_of - dv.last_seen).days if dv.last_seen else None
        u = d.by_upn.get(dv.user)
        if age is None or age > s.last_seen_max_days * 4:
            blockers.append(f"not seen for {age if age is not None else '?'} days: probably retired, confirm or remove")
        elif age > s.last_seen_max_days:
            actions.append(f"not seen for {age} days: switch on and sync before the wave")
        if dv.kind != "virtual" and (not dv.tpm or not dv.secure_boot or 0 < dv.ram_gb < s.min_ram_gb):
            why = ", ".join(x for x, bad in (("no TPM 2.0", not dv.tpm), ("Secure Boot off", not dv.secure_boot),
                                             (f"{dv.ram_gb:g} GB RAM", 0 < dv.ram_gb < s.min_ram_gb)) if bad)
            blockers.append(f"not Windows 11 capable ({why}): replace or exempt")
        elif dv.os_build and dv.os_build < s.min_os_build:
            actions.append(f"OS build {dv.os_build} below {s.min_os_build}: upgrade in the wave (Autopilot reset installs the target image)")
        if not dv.autopilot:
            blockers.append("not registered in Autopilot: upload the hardware hash to the target tenant")
        if not dv.bitlocker_key:
            blockers.append("BitLocker recovery key not escrowed: back up the key before the reset")
        if dv.disk_free_gb and dv.disk_free_gb < s.min_disk_free_gb:
            actions.append(f"{dv.disk_free_gb:g} GB free: clean up or the reset may fail")
        if dv.join_type in ("domain", "workgroup", "registered"):
            actions.append(f"{dv.join_type}-joined: full reset and Autopilot enrolment, no in-place move")
        if dv.management == "none":
            actions.append("no management agent: enrol or reset manually on the day")
        if not dv.compliant:
            actions.append("non-compliant in the source tenant: fix before the move or it will be blocked in the target")
        if dv.local_admins:
            actions.append(f"local admins ({', '.join(dv.local_admins)}): will be lost on reset, re-grant through EPM if still needed")
        if dv.kind in ("kiosk", "shared"):
            actions.append(f"{dv.kind} device: no primary user, use the shared-device procedure")
        unknown = [a for a in dv.apps if s.app_catalog and _app_status(a, s) in ("unknown", "blocked")]
        if unknown:
            actions.append("apps without a target decision: " + ", ".join(sorted(set(unknown))))
        if dv.user and not u:
            notes.append("primary user not in users.csv")
        if u and u.leaving:
            blockers.clear(); actions.clear()
            verdict = EXCLUDE
            notes.append("user is leaving: do not migrate, collect the device")
        elif blockers:
            verdict = BLOCKED
        elif actions:
            verdict = ACTIONS
        else:
            verdict = READY
        rows.append(dict(device=dv, name=dv.name, user=dv.user, site=dv.site, wave=dv.wave or "unassigned", vip=dv.vip or bool(u and u.vip),
                         age=age, verdict=verdict, blockers=blockers, actions=actions, notes=notes))
    return rows


def _app_status(app: str, s: Settings) -> str:
    v = s.app_catalog.get(app.strip().lower())
    if v is None:
        return "unknown"
    v = v.strip().lower()
    if v.startswith("replace"):
        return "replace"
    return v if v in ("approved", "blocked") else "unknown"


def apps(d: Data) -> list[dict]:
    """Every application seen on the devices, with its target decision and how many devices carry it."""
    s = d.settings
    count: Counter = Counter()
    for dv in d.devices:
        for a in set(x.strip() for x in dv.apps):
            count[a] += 1
    rows = []
    for a, n in count.most_common():
        st = _app_status(a, s)
        dec = s.app_catalog.get(a.lower(), "")
        rows.append(dict(app=a, devices=n, status=st, decision=dec.split(":", 1)[1].strip() if ":" in dec else dec))
    return rows


# ---------------------------------------------------------------- users

def user_readiness(d: Data) -> list[dict]:
    s = d.settings
    wave_by_shared: dict[str, set[str]] = defaultdict(set)
    for u in d.users:
        for m in u.shared_mailboxes:
            wave_by_shared[m].add(u.wave or "unassigned")
    rows = []
    for u in d.users:
        blockers, actions, notes = [], [], []
        days = (d.as_of - u.last_signin).days if u.last_signin else None
        if u.leaving:
            rows.append(dict(user=u, upn=u.upn, wave=u.wave or "unassigned", vip=u.vip, verdict=EXCLUDE, blockers=[], actions=[],
                             notes=["leaving: disable in the source tenant, do not migrate"], mailbox_gb=u.mailbox_gb, days=days))
            continue
        if u.account_type not in ("member", "user", ""):
            notes.append(f"{u.account_type} account: handled by the identity workstream, not in a wave")
        if days is not None and days > s.dormant_user_days:
            actions.append(f"no sign-in for {days} days: confirm with the manager before migrating")
        if not u.mfa:
            blockers.append("no MFA method registered: will not pass Conditional Access in the target on day 1")
        if u.mailbox_gb > s.mailbox_max_gb:
            blockers.append(f"mailbox {u.mailbox_gb:g} GB over {s.mailbox_max_gb:g}: pre-stage the migration a week before the wave")
        if u.onedrive_gb > s.onedrive_max_gb:
            actions.append(f"OneDrive {u.onedrive_gb:g} GB: start the copy early and tell the user not to reorganise folders")
        missing = [lic for lic in u.licences if s.licence_map and s.licence_map.get(lic, None) in (None, "")]
        if missing:
            blockers.append("licence without a target mapping: " + ", ".join(missing))
        split = [m for m in u.shared_mailboxes if len(wave_by_shared[m]) > 1]
        if split:
            actions.append("shared mailbox with users in several waves: " + ", ".join(sorted(split)) + " (move it with the last wave or split access)")
        if u.vip:
            actions.append("VIP: white-glove slot, migrate the device the evening before and test with the user")
        if u.manager and u.manager in d.by_upn and d.by_upn[u.manager].wave and u.wave and d.by_upn[u.manager].wave != u.wave:
            notes.append(f"manager ({u.manager}) moves in {d.by_upn[u.manager].wave}")
        if not d.devices_of.get(u.upn):
            notes.append("no device in the inventory")
        verdict = BLOCKED if blockers else ACTIONS if actions else READY
        rows.append(dict(user=u, upn=u.upn, wave=u.wave or "unassigned", vip=u.vip, verdict=verdict, blockers=blockers, actions=actions,
                         notes=notes, mailbox_gb=u.mailbox_gb, days=days))
    return rows


# ---------------------------------------------------------------- waves and capacity

def wave_plan(d: Data, dev_rows: list[dict] | None = None, usr_rows: list[dict] | None = None) -> list[dict]:
    s = d.settings
    dev_rows = dev_rows if dev_rows is not None else device_readiness(d)
    usr_rows = usr_rows if usr_rows is not None else user_readiness(d)
    plan = []
    for w in s.waves:
        dv = [r for r in dev_rows if r["wave"] == w.name and r["verdict"] != EXCLUDE]
        us = [r for r in usr_rows if r["wave"] == w.name and r["verdict"] != EXCLUDE]
        n_dev, n_usr = len(dv), len(us)
        blocked_dev = sum(r["verdict"] == BLOCKED for r in dv)
        blocked_usr = sum(r["verdict"] == BLOCKED for r in us)
        capacity = w.techs * s.devices_per_tech_day * w.days
        days_needed = math.ceil(n_dev / (w.techs * s.devices_per_tech_day)) if n_dev else 0
        mail_gb = sum(r["mailbox_gb"] for r in us)
        big = sum(r["mailbox_gb"] > s.mailbox_max_gb for r in us)
        mail_hours = mail_gb / s.migration_gb_per_hour if s.migration_gb_per_hour else 0
        vips = sum(r["vip"] for r in dv)
        freeze = in_freeze(w.date, s, w.days)
        blocked_pct = round(100 * blocked_dev / n_dev, 1) if n_dev else 0.0
        reasons = []
        if freeze:
            reasons.append(f"falls in the freeze {freeze[0]:%d %b}–{freeze[1]:%d %b}")
        if n_dev > capacity:
            reasons.append(f"{n_dev} devices for a capacity of {capacity} ({w.techs} techs × {s.devices_per_tech_day}/day × {w.days} d)")
        if blocked_pct > s.blocked_share_max:
            reasons.append(f"{blocked_pct:g}% of devices blocked (limit {s.blocked_share_max:g}%)")
        if mail_hours > s.window_hours:
            reasons.append(f"{mail_gb:,.0f} GB of mailboxes need {mail_hours:.0f} h at {s.migration_gb_per_hour:g} GB/h, window is {s.window_hours:g} h")
        if w.pilot and not all(s.prerequisites.values()):
            reasons.append("target tenant prerequisites not complete")
        verdict = "go" if not reasons else "no-go"
        if verdict == "go" and (blocked_dev or blocked_usr):
            verdict = "go with actions"
        plan.append(dict(wave=w, name=w.name, date=w.date, devices=n_dev, users=n_usr, blocked_devices=blocked_dev, blocked_users=blocked_usr,
                         blocked_pct=blocked_pct, capacity=capacity, days_needed=days_needed, mailbox_gb=round(mail_gb, 1),
                         mailbox_hours=round(mail_hours, 1), big_mailboxes=big, vips=vips, freeze=freeze, verdict=verdict, reasons=reasons))
    return plan


def unassigned(d: Data, dev_rows: list[dict] | None = None, usr_rows: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    dev_rows = dev_rows if dev_rows is not None else device_readiness(d)
    usr_rows = usr_rows if usr_rows is not None else user_readiness(d)
    return ([r for r in dev_rows if r["wave"] == "unassigned" and r["verdict"] != EXCLUDE],
            [r for r in usr_rows if r["wave"] == "unassigned" and r["verdict"] != EXCLUDE])


# ---------------------------------------------------------------- findings

def _top(rows: list[dict], key: str, n: int = 6) -> str:
    return ", ".join(r[key] for r in rows[:n]) + (f" and {len(rows) - n} more" if len(rows) > n else "")


def run_all(d: Data) -> list[Finding]:
    s = d.settings
    assign_waves(d)
    dev, usr = device_readiness(d), user_readiness(d)
    plan = wave_plan(d, dev, usr)
    f: list[Finding] = []

    # prerequisites
    missing = [k for k, v in s.prerequisites.items() if not v]
    if missing:
        f.append(Finding(CRITICAL, "Prerequisites", "PRE-01", "Target tenant not ready", f"{len(missing)} of {len(s.prerequisites)} prerequisites "
                         f"still open: {', '.join(missing)}.", "Close them before the pilot; every wave inherits them.", "Programme lead", items=missing))
    # devices
    texts = {"not registered in Autopilot": ("DEV-01", "Devices not in Autopilot", "Upload the hardware hashes (Get-WindowsAutopilotInfo or the Intune export) to the target tenant; without it the reset lands in OOBE with no profile.", "Endpoint engineering"),
             "BitLocker recovery key not escrowed": ("DEV-02", "BitLocker keys not escrowed", "Run manage-bde -protectors -adbackup (or the Intune policy) and confirm the key in Entra before the reset; a reset without the key loses the disk if it fails.", "Endpoint engineering"),
             "not Windows 11 capable": ("DEV-03", "Hardware not Windows 11 capable", "Replace (lead time!) or document an exemption on Windows 10 in the target; do not put them in a wave until decided.", "Asset management")}
    by_reason: dict[str, list[dict]] = defaultdict(list)
    for r in dev:
        for b in r["blockers"]:
            key = next((k for k in texts if b.startswith(k)), "not seen for" if b.startswith("not seen for") else b.split(":")[0])
            if r not in by_reason[key]:
                by_reason[key].append(r)
    for key, rows in by_reason.items():
        if key in texts:
            code, subj, fix, owner = texts[key]
            f.append(Finding(CRITICAL if len(rows) > 10 else WARNING, "Devices", code, subj, f"{len(rows)} devices: {_top(rows, 'name')}.", fix, owner,
                             items=[r["name"] for r in rows]))
        elif key == "not seen for":
            f.append(Finding(WARNING, "Devices", "DEV-04", "Devices not seen for weeks", f"{len(rows)} devices have not checked in for more than "
                             f"{s.last_seen_max_days * 4} days: {_top(rows, 'name')}.", "Confirm with the site whether they exist; retire the rest so the wave counts are real.",
                             "Site IT", items=[r["name"] for r in rows]))
    legacy = [r for r in dev if r["device"].join_type in ("domain", "workgroup", "registered") and r["verdict"] != EXCLUDE]
    if legacy:
        f.append(Finding(WARNING, "Devices", "DEV-05", "Domain-only or unmanaged devices", f"{len(legacy)} devices are not hybrid or Entra joined: "
                         f"{_top(legacy, 'name')}.", "They need a full reset on the day (no in-place tenant move); plan 2× the time per device and a kiosk/shared procedure.",
                         "Endpoint engineering", items=[r["name"] for r in legacy]))
    admins = [r for r in dev if r["device"].local_admins]
    if admins:
        f.append(Finding(INFO, "Devices", "DEV-06", "Local administrators present", f"{len(admins)} devices have local admins that the reset will remove.",
                         "Decide with EPM in the target which ones are re-granted; most are not.", "Endpoint engineering"))
    excl = [r for r in dev if r["verdict"] == EXCLUDE]
    if excl:
        f.append(Finding(INFO, "Devices", "DEV-07", "Devices of leavers", f"{len(excl)} devices belong to users who are leaving.",
                         "Collect them before the wave; they do not count for capacity.", "Site IT", items=[r["name"] for r in excl]))
    # users
    nomfa = [r for r in usr if any(b.startswith("no MFA") for b in r["blockers"])]
    if nomfa:
        f.append(Finding(CRITICAL if len(nomfa) > 20 else WARNING, "Users & mailboxes", "USR-01", "Users without MFA", f"{len(nomfa)} users have no MFA "
                         f"method registered: {_top(nomfa, 'upn')}.", "Registration campaign two weeks before each wave; the target Conditional Access blocks them on day 1.",
                         "Identity", items=[r["upn"] for r in nomfa]))
    bigm = [r for r in usr if r["mailbox_gb"] > s.mailbox_max_gb and r["verdict"] != EXCLUDE]
    if bigm:
        f.append(Finding(WARNING, "Users & mailboxes", "USR-02", "Large mailboxes", f"{len(bigm)} mailboxes over {s.mailbox_max_gb:g} GB "
                         f"({sum(r['mailbox_gb'] for r in bigm):,.0f} GB in total): {_top(bigm, 'upn')}.",
                         "Pre-stage them a week before their wave (incremental sync on the day) or archive first.", "Messaging", items=[r["upn"] for r in bigm]))
    lic = [r for r in usr if any(b.startswith("licence without") for b in r["blockers"])]
    if lic:
        skus = Counter(x for r in lic for b in r["blockers"] if b.startswith("licence without") for x in b.split(":", 1)[1].split(","))
        f.append(Finding(CRITICAL, "Users & mailboxes", "USR-03", "Licences without a target mapping", f"{len(lic)} users carry a licence with no "
                         f"equivalent in licence_map: {', '.join(k.strip() for k, _ in skus.most_common())}.",
                         "Buy or map the SKU in the target before the wave, or the user loses the product on day 1.", "Licensing", items=[r["upn"] for r in lic]))
    split = sorted({m for r in usr for a in r["actions"] if a.startswith("shared mailbox") for m in a.split(":", 1)[1].split("(")[0].split(",")})
    if split:
        f.append(Finding(WARNING, "Users & mailboxes", "USR-04", "Shared mailboxes split across waves", f"{len(split)} shared mailboxes have members in "
                         f"more than one wave: {', '.join(x.strip() for x in split[:6])}.", "Move each with the wave of its last member and keep cross-tenant access until then.",
                         "Messaging", items=[x.strip() for x in split]))
    dormant = [r for r in usr if any(a.startswith("no sign-in") for a in r["actions"])]
    if dormant:
        f.append(Finding(INFO, "Users & mailboxes", "USR-05", "Dormant users", f"{len(dormant)} users have not signed in for {s.dormant_user_days}+ days.",
                         "Confirm with managers; do not migrate what nobody uses.", "Identity", items=[r["upn"] for r in dormant]))
    leavers = [r for r in usr if r["verdict"] == EXCLUDE]
    if leavers:
        f.append(Finding(INFO, "Users & mailboxes", "USR-06", "Leavers", f"{len(leavers)} users are leaving and are excluded from the waves.",
                         "Disable in the source after their last day; export the mailbox if legal asks.", "HR / Identity", items=[r["upn"] for r in leavers]))
    # apps
    ap = apps(d)
    unk = [a for a in ap if a["status"] == "unknown"]
    blk = [a for a in ap if a["status"] == "blocked"]
    if unk:
        f.append(Finding(CRITICAL if any(a["devices"] >= 20 for a in unk) else WARNING, "Applications", "APP-01", "Applications without a target decision",
                         f"{len(unk)} apps on {sum(a['devices'] for a in unk)} device-installs: " + ", ".join(f"{a['app']} ({a['devices']})" for a in unk[:6]) + ".",
                         "Decide approved / replace / blocked in app_catalog and package the approved ones in the target Intune before the wave.",
                         "Application packaging", items=[a["app"] for a in unk]))
    if blk:
        f.append(Finding(INFO, "Applications", "APP-02", "Blocked applications", f"{len(blk)} apps will not be available in the target: " +
                         ", ".join(a["app"] for a in blk) + ".", "Tell the users before their wave; a surprise on Monday is a ticket.", "Change management"))
    # waves
    for p in plan:
        if p["verdict"] == "no-go":
            f.append(Finding(CRITICAL, "Waves & capacity", "WAV-01", f"{p['name']} ({p['date']:%d %b}): no-go", "; ".join(p["reasons"]) + ".",
                             "Move the date, add technicians or clear the blockers; decide at the wave readiness review a week before.", "Programme lead", wave=p["name"]))
        elif p["verdict"] == "go with actions":
            f.append(Finding(WARNING, "Waves & capacity", "WAV-02", f"{p['name']} ({p['date']:%d %b}): go with actions",
                             f"{p['blocked_devices']} devices and {p['blocked_users']} users still blocked.", "Clear the blockers list for this wave in the readiness review.",
                             "Wave lead", wave=p["name"]))
    ud, uu = unassigned(d, dev, usr)
    if ud or uu:
        f.append(Finding(WARNING, "Waves & capacity", "WAV-03", "Not assigned to any wave", f"{len(ud)} devices and {len(uu)} users match no wave "
                         f"(site or department not in settings): {_top(ud, 'name')}.", "Add their site or department to a wave, or a final catch-all wave.",
                         "Programme lead", items=[r["name"] for r in ud] + [r["upn"] for r in uu]))
    vips = sum(p["vips"] for p in plan)
    if vips:
        f.append(Finding(INFO, "Waves & capacity", "WAV-04", "VIP devices", f"{vips} VIP devices across the waves.",
                         "One technician per VIP the evening before, with the user's calendar checked.", "Wave lead"))
    order = {CRITICAL: 0, WARNING: 1, INFO: 2}
    f.sort(key=lambda x: (order[x.severity], AREAS.index(x.area), x.code))
    return f


def area_status(findings: list[Finding]) -> dict[str, str]:
    st = {a: "ok" for a in AREAS}
    for x in findings:
        if x.severity == CRITICAL:
            st[x.area] = CRITICAL
        elif x.severity == WARNING and st[x.area] != CRITICAL:
            st[x.area] = WARNING
    return st
