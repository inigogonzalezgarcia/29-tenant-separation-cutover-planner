"""Outputs: report.html, cutover-plan.md, waves.csv, device-readiness.csv, user-readiness.csv, apps.csv, blockers.csv, actions.csv, findings.csv."""
from __future__ import annotations

import csv
import html
from pathlib import Path

from .checks import (ACTIONS, AREAS, BLOCKED, CRITICAL, EXCLUDE, INFO, READY, WARNING, Finding, apps, area_status, assign_waves,
                     device_readiness, unassigned, user_readiness, wave_plan)
from .model import Data

e = html.escape


def build(d: Data, findings: list[Finding]) -> dict:
    """Everything the writers need, computed once."""
    assign_waves(d)
    dev, usr = device_readiness(d), user_readiness(d)
    plan = wave_plan(d, dev, usr)
    ud, uu = unassigned(d, dev, usr)
    return dict(dev=dev, usr=usr, plan=plan, apps=apps(d), unassigned_dev=ud, unassigned_usr=uu, findings=findings)


def summary(d: Data, findings: list[Finding], b: dict | None = None) -> dict:
    b = b or build(d, findings)
    dev, usr, plan = b["dev"], b["usr"], b["plan"]
    cnt = lambda rows, v: sum(r["verdict"] == v for r in rows)  # noqa: E731
    return dict(devices=len(dev), users=len(usr), waves=len(plan),
                dev_ready=cnt(dev, READY), dev_actions=cnt(dev, ACTIONS), dev_blocked=cnt(dev, BLOCKED), dev_exclude=cnt(dev, EXCLUDE),
                usr_ready=cnt(usr, READY), usr_actions=cnt(usr, ACTIONS), usr_blocked=cnt(usr, BLOCKED), usr_exclude=cnt(usr, EXCLUDE),
                go=sum(p["verdict"] == "go" for p in plan), go_actions=sum(p["verdict"] == "go with actions" for p in plan),
                nogo=sum(p["verdict"] == "no-go" for p in plan), unassigned=len(b["unassigned_dev"]) + len(b["unassigned_usr"]),
                apps_unknown=sum(a["status"] == "unknown" for a in b["apps"]), mailbox_gb=round(sum(r["mailbox_gb"] for r in usr), 1),
                critical=sum(f.severity == CRITICAL for f in findings), warning=sum(f.severity == WARNING for f in findings),
                info=sum(f.severity == INFO for f in findings))


# ---------------------------------------------------------------- csv

def _w(path: Path, header: list[str], rows: list[list]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def write_findings(findings: list[Finding], path: Path) -> None:
    _w(path, ["severity", "area", "code", "subject", "detail", "next_step", "owner", "wave", "items"],
       [[f.severity, f.area, f.code, f.subject, f.detail, f.fix, f.owner, f.wave, "; ".join(f.items)] for f in findings])


def write_actions(findings: list[Finding], path: Path) -> int:
    rows = [[f.code, f.subject, f.fix, f.owner, f.wave, f.severity, "", ""] for f in findings if f.severity != INFO]
    _w(path, ["ref", "action", "next_step", "owner", "wave", "severity", "due", "done"], rows)
    return len(rows)


def write_waves(b: dict, path: Path) -> None:
    _w(path, ["wave", "date", "days", "techs", "devices", "users", "blocked_devices", "blocked_pct", "blocked_users", "capacity", "days_needed",
              "mailbox_gb", "mailbox_hours", "big_mailboxes", "vips", "verdict", "reasons"],
       [[p["name"], p["date"].isoformat(), p["wave"].days, p["wave"].techs, p["devices"], p["users"], p["blocked_devices"], p["blocked_pct"],
         p["blocked_users"], p["capacity"], p["days_needed"], p["mailbox_gb"], p["mailbox_hours"], p["big_mailboxes"], p["vips"], p["verdict"],
         "; ".join(p["reasons"])] for p in b["plan"]])


def write_devices(b: dict, path: Path) -> None:
    _w(path, ["device", "user", "site", "wave", "vip", "join_type", "management", "os_build", "last_seen_days", "verdict", "blockers", "actions", "notes"],
       [[r["name"], r["user"], r["site"], r["wave"], "yes" if r["vip"] else "no", r["device"].join_type, r["device"].management, r["device"].os_build,
         "" if r["age"] is None else r["age"], r["verdict"], "; ".join(r["blockers"]), "; ".join(r["actions"]), "; ".join(r["notes"])] for r in b["dev"]])


def write_users(b: dict, path: Path) -> None:
    _w(path, ["user", "name", "department", "site", "wave", "vip", "mailbox_gb", "onedrive_gb", "mfa", "last_signin_days", "verdict", "blockers", "actions", "notes"],
       [[r["upn"], r["user"].name, r["user"].department, r["user"].site, r["wave"], "yes" if r["vip"] else "no", r["mailbox_gb"], r["user"].onedrive_gb,
         "yes" if r["user"].mfa else "no", "" if r["days"] is None else r["days"], r["verdict"], "; ".join(r["blockers"]), "; ".join(r["actions"]),
         "; ".join(r["notes"])] for r in b["usr"]])


def write_apps(b: dict, path: Path) -> None:
    _w(path, ["app", "devices", "status", "decision"], [[a["app"], a["devices"], a["status"], a["decision"]] for a in b["apps"]])


def write_blockers(b: dict, path: Path) -> None:
    rows = []
    for r in b["dev"]:
        rows += [["device", r["name"], r["user"], r["wave"], x, "", ""] for x in r["blockers"]]
    for r in b["usr"]:
        rows += [["user", r["upn"], "", r["wave"], x, "", ""] for x in r["blockers"]]
    rows.sort(key=lambda x: (x[3], x[0], x[1]))
    _w(path, ["kind", "item", "user", "wave", "blocker", "owner", "cleared"], rows)


# ---------------------------------------------------------------- markdown plan

def plan_md(d: Data, findings: list[Finding], b: dict | None = None) -> str:
    b = b or build(d, findings)
    s = d.settings
    sm = summary(d, findings, b)
    L = [f"# Cutover plan · {s.programme}", "",
         f"{s.source_tenant} → {s.target_tenant} · inventory as of {d.as_of:%d %b %Y} · {sm['devices']} devices, {sm['users']} users, {sm['waves']} waves.",
         "", "## Summary", "",
         f"- Devices: {sm['dev_ready']} ready · {sm['dev_actions']} ready with actions · {sm['dev_blocked']} blocked · {sm['dev_exclude']} excluded (leavers).",
         f"- Users: {sm['usr_ready']} ready · {sm['usr_actions']} with actions · {sm['usr_blocked']} blocked · {sm['usr_exclude']} leaving.",
         f"- Waves: {sm['go']} go · {sm['go_actions']} go with actions · {sm['nogo']} no-go · {sm['unassigned']} items in no wave.",
         f"- Mailboxes: {sm['mailbox_gb']:,.0f} GB to move · {sm['apps_unknown']} applications without a target decision.", ""]
    if s.prerequisites:
        L += ["## Target tenant prerequisites", ""] + [f"- [{'x' if v else ' '}] {k}" for k, v in s.prerequisites.items()] + [""]
    L += ["## Waves", ""]
    for p in b["plan"]:
        w = p["wave"]
        L += [f"### {p['name']} · {p['date']:%A %d %B %Y} · **{p['verdict'].upper()}**", "",
              f"- Scope: {', '.join(w.sites) or '–'}" + (f"; departments {', '.join(w.departments)}" if w.departments else "") + (" · pilot" if w.pilot else ""),
              f"- {p['devices']} devices ({p['blocked_devices']} blocked, {p['vips']} VIP) · {p['users']} users ({p['blocked_users']} blocked)",
              f"- Capacity: {w.techs} technicians × {s.devices_per_tech_day}/day × {w.days} day(s) = {p['capacity']}; needs {p['days_needed']} day(s)",
              f"- Mailboxes: {p['mailbox_gb']:,.0f} GB, about {p['mailbox_hours']:g} h at {s.migration_gb_per_hour:g} GB/h ({p['big_mailboxes']} to pre-stage)"]
        if p["reasons"]:
            L += ["- Why not go: " + "; ".join(p["reasons"])]
        blk = [r for r in b["dev"] if r["wave"] == p["name"] and r["verdict"] == BLOCKED]
        if blk:
            L += ["- Blocked devices: " + ", ".join(r["name"] for r in blk[:10]) + (f" and {len(blk) - 10} more" if len(blk) > 10 else "") + " (see blockers.csv)"]
        L.append("")
    L += ["## Cutover day, in order", "",
          "1. T-7: wave readiness review with this report; every blocker in `blockers.csv` has an owner or the item leaves the wave.",
          "2. T-2: pre-staged mailboxes synced; Autopilot profiles assigned in the target; hardware hashes confirmed; users told what to bring and what to back up (nothing, if OneDrive is synced).",
          "3. T-1 evening: VIP devices, one technician each. Freeze on changes in the source tenant for the wave's users.",
          "4. Day, 07:00: source accounts blocked for sign-in; final mailbox delta; licences assigned in the target.",
          "5. Day, 08:00–: devices reset (Autopilot), users sign in with the target UPN, MFA re-registered if not pre-registered, OneDrive re-synced, Teams/Outlook profiles recreated.",
          "6. Day, 17:00: wave checkpoint: devices enrolled and compliant in the target, users signed in, tickets open; decide on the fallback for anyone stuck (loan device, source account re-enabled for 24 h).",
          "7. T+1: hypercare desk on site; shared mailboxes moved; blockers list closed; lessons into the next wave's review.", "",
          "## Findings", ""]
    for f in findings:
        L.append(f"- **{f.severity.upper()}** [{f.area}] {f.subject}: {f.detail} _Next:_ {f.fix} ({f.owner or '?'})")
    L += ["", "Generated by cutover from devices.csv, users.csv and settings.json. Fictional demo data unless you ran it on your own inventory."]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- html

CSS = """
:root{--bg:#f6f7f8;--card:#fff;--ink:#1b2128;--muted:#66717c;--line:#e2e5e9;--ok:#1e7a4c;--warn:#a46200;--bad:#b42318;--info:#0b6bcb;--na:#8a94a0}
@media (prefers-color-scheme:dark){:root{--bg:#13171b;--card:#1b2127;--ink:#e7eaee;--muted:#9aa5b0;--line:#2b333b;--ok:#4cc38a;--warn:#f0a83a;--bad:#f2685c;--info:#5aa9f0;--na:#6d7782}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:28px 18px 60px}h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:34px 0 12px}
.muted{color:var(--muted)}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:12px;margin-top:20px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}.tile b{display:block;font-size:26px}
.areas{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.area{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--ok);border-radius:10px;padding:10px 14px}
.area.critical{border-left-color:var(--bad)}.area.warning{border-left-color:var(--warn)}
.area .lbl{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin-bottom:4px}.area .cnt{display:block;font-size:13px;color:var(--muted);margin-top:4px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
tr:last-child td{border-bottom:0}.wrap{overflow-x:auto}.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.pill{display:inline-block;white-space:nowrap;padding:1px 8px;border-radius:99px;font-size:12px;font-weight:600;border:1px solid currentColor}
.critical{color:var(--bad)}.warning{color:var(--warn)}.info{color:var(--info)}.ok{color:var(--ok)}.na{color:var(--na)}
.area.critical,.area.warning,.area.ok{color:inherit}
.stack{display:flex;height:12px;border-radius:6px;overflow:hidden;background:var(--line);min-width:160px}.stack i{display:block;height:100%}
.s0{background:var(--ok)}.s1{background:#e3b45c}.s2{background:var(--bad)}.s3{background:var(--na)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px}.grid div{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.grid b{display:block;font-size:22px}.tag{display:inline-block;font-size:12px;padding:0 6px;margin:2px 4px 0 0;border-radius:5px;background:var(--line)}
td.nw{white-space:nowrap}td.fix{font-size:13px;color:var(--muted);max-width:380px}code{font-size:12px}ul.chk{margin:0;padding-left:18px}
footer{margin-top:40px;font-size:13px}
"""


def _pill(s: str, label: str | None = None) -> str:
    cls = {"go": "ok", "go with actions": "warning", "no-go": "critical", READY: "ok", ACTIONS: "warning", BLOCKED: "critical", EXCLUDE: "na"}.get(s, s)
    return f"<span class='pill {cls}'>{e(label or ('OK' if s == 'ok' else s.upper()))}</span>"


def _stack(rows: list[dict]) -> str:
    n = len(rows) or 1
    parts = ""
    for i, v in enumerate((READY, ACTIONS, BLOCKED, EXCLUDE)):
        k = sum(r["verdict"] == v for r in rows)
        parts += f"<i class='s{i}' style='width:{100 * k / n:.0f}%' title='{v}: {k}'></i>"
    return f"<div class='stack'>{parts}</div>"


def render_html(d: Data, findings: list[Finding], title: str, b: dict | None = None) -> str:
    b = b or build(d, findings)
    s = d.settings
    sm, st = summary(d, findings, b), area_status(findings)
    h = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>{e(title)}</title><style>{CSS}</style></head><body><main>",
         f"<h1>{e(title)}</h1><p class='muted'>{e(s.source_tenant)} → {e(s.target_tenant)} · inventory as of {d.as_of:%d %b %Y}</p>",
         "<div class='tiles'>"]
    tiles = [("Devices", str(sm["devices"]), f"{sm['dev_ready']} ready · {sm['dev_actions']} with actions"),
             ("Devices blocked", str(sm["dev_blocked"]), f"{sm['dev_exclude']} excluded (leavers)"),
             ("Users", str(sm["users"]), f"{sm['usr_ready']} ready · {sm['usr_actions']} with actions"),
             ("Users blocked", str(sm["usr_blocked"]), f"{sm['usr_exclude']} leaving"),
             ("Waves", f"{sm['go'] + sm['go_actions']}/{sm['waves']}", f"go · {sm['nogo']} no-go"),
             ("Mailboxes", f"{sm['mailbox_gb']:,.0f} GB", f"{s.migration_gb_per_hour:g} GB/h"),
             ("Apps undecided", str(sm["apps_unknown"]), f"of {len(b['apps'])} seen"),
             ("Not in a wave", str(sm["unassigned"]), "devices + users"),
             ("Critical", str(sm["critical"]), f"{sm['warning']} warnings · {sm['info']} for information")]
    h += [f"<div class='tile'><span class='muted'>{e(a)}</span><b>{e(x)}</b><span class='muted'>{e(y)}</span></div>" for a, x, y in tiles]
    h.append("</div><h2>Status by area</h2><div class='areas'>")
    for a in AREAS:
        n = sum(f.area == a and f.severity != INFO for f in findings)
        h.append(f"<div class='area {st[a]}'><span class='lbl'>{e(a)}</span>{_pill(st[a])}<span class='cnt'>{n} to act on</span></div>")
    h.append("</div>")
    act = [f for f in findings if f.severity == CRITICAL]
    if act:
        h.append("<h2>Fix first</h2><div class='wrap'><table><tr><th>Subject</th><th>What</th><th>Next step</th><th>Owner</th></tr>")
        h += [f"<tr><td><b>{e(f.subject)}</b><br><span class='muted'>{e(f.area)}</span></td><td>{e(f.detail)}</td><td class='fix'>{e(f.fix)}</td>"
              f"<td class='nw'>{e(f.owner or '?')}</td></tr>" for f in act]
        h.append("</table></div>")
    # waves
    h.append("<h2>Waves</h2><div class='wrap'><table><tr><th>Wave</th><th>Date</th><th class='num'>Devices</th><th>Readiness</th><th class='num'>Users</th>"
             "<th class='num'>Capacity</th><th class='num'>Mail</th><th class='num'>VIP</th><th>Verdict</th></tr>")
    for p in b["plan"]:
        rows = [r for r in b["dev"] if r["wave"] == p["name"]]
        cap_cls = "critical" if p["devices"] > p["capacity"] else ""
        h.append(f"<tr><td><b>{e(p['name'])}</b><br><span class='muted'>{e(', '.join(p['wave'].sites) or '–')}{' · pilot' if p['wave'].pilot else ''}</span></td>"
                 f"<td class='nw'>{p['date']:%a %d %b}<br><span class='muted'>{p['wave'].days} d · {p['wave'].techs} techs</span></td>"
                 f"<td class='num'>{p['devices']}<br><span class='muted'>{p['blocked_devices']} blocked</span></td><td>{_stack(rows)}</td>"
                 f"<td class='num'>{p['users']}<br><span class='muted'>{p['blocked_users']} blocked</span></td>"
                 f"<td class='num {cap_cls}'>{p['capacity']}<br><span class='muted'>{p['days_needed']} d needed</span></td>"
                 f"<td class='num'>{p['mailbox_gb']:,.0f} GB<br><span class='muted'>{p['mailbox_hours']:g} h</span></td><td class='num'>{p['vips']}</td>"
                 f"<td>{_pill(p['verdict'])}{('<br><span class=muted>' + e('; '.join(p['reasons'])) + '</span>') if p['reasons'] else ''}</td></tr>")
    h.append("</table></div><p class='muted'>Readiness bar, left to right: ready · with actions · blocked · excluded. Capacity = technicians × "
             f"{s.devices_per_tech_day} devices/day × days. Mail hours at {s.migration_gb_per_hour:g} GB/h against a {s.window_hours:g} h window.</p>")
    if s.prerequisites:
        h.append("<h2>Target tenant prerequisites</h2><div class='grid'><div><ul class='chk'>" + "".join(
            f"<li class='{'ok' if v else 'critical'}'>{'✓' if v else '✗'} {e(k)}</li>" for k, v in s.prerequisites.items()) + "</ul></div></div>")
    # blocked devices
    blk = [r for r in b["dev"] if r["verdict"] == BLOCKED]
    if blk:
        h.append(f"<h2>Blocked devices ({len(blk)})</h2><div class='wrap'><table><tr><th>Device</th><th>User</th><th>Wave</th><th>Blockers</th><th>Also</th></tr>")
        for r in sorted(blk, key=lambda r: (r["wave"], r["name"]))[:30]:
            h.append(f"<tr><td class='nw'>{e(r['name'])}{' <span class=tag>VIP</span>' if r['vip'] else ''}<br><span class='muted'>{e(r['site'])}</span></td>"
                     f"<td>{e(r['user'] or '–')}</td><td class='nw'>{e(r['wave'])}</td><td>{'<br>'.join(e(x) for x in r['blockers'])}</td>"
                     f"<td class='fix'>{'<br>'.join(e(x) for x in r['actions'])}</td></tr>")
        h.append("</table></div>" + (f"<p class='muted'>Showing 30 of {len(blk)}; the full list is in <code>blockers.csv</code>.</p>" if len(blk) > 30 else ""))
    # blocked users
    bu = [r for r in b["usr"] if r["verdict"] == BLOCKED]
    if bu:
        h.append(f"<h2>Blocked users ({len(bu)})</h2><div class='wrap'><table><tr><th>User</th><th>Wave</th><th class='num'>Mailbox</th><th>Blockers</th><th>Also</th></tr>")
        for r in sorted(bu, key=lambda r: (r["wave"], r["upn"]))[:30]:
            h.append(f"<tr><td class='nw'>{e(r['upn'])}{' <span class=tag>VIP</span>' if r['vip'] else ''}<br><span class='muted'>{e(r['user'].department)}</span></td>"
                     f"<td class='nw'>{e(r['wave'])}</td><td class='num'>{r['mailbox_gb']:g} GB</td><td>{'<br>'.join(e(x) for x in r['blockers'])}</td>"
                     f"<td class='fix'>{'<br>'.join(e(x) for x in r['actions'])}</td></tr>")
        h.append("</table></div>" + (f"<p class='muted'>Showing 30 of {len(bu)}; see <code>user-readiness.csv</code>.</p>" if len(bu) > 30 else ""))
    # apps
    h.append("<h2>Applications</h2><div class='wrap'><table><tr><th>Application</th><th class='num'>Devices</th><th>Status</th><th>Decision</th></tr>")
    for a in b["apps"][:40]:
        cls = {"approved": "ok", "replace": "warning", "blocked": "critical", "unknown": "critical"}[a["status"]]
        h.append(f"<tr><td>{e(a['app'])}</td><td class='num'>{a['devices']}</td><td><span class='pill {cls}'>{e(a['status'].upper())}</span></td>"
                 f"<td class='fix'>{e(a['decision'] or ('decide in app_catalog' if a['status'] == 'unknown' else ''))}</td></tr>")
    h.append("</table></div>" + (f"<p class='muted'>Top 40 of {len(b['apps'])}; all in <code>apps.csv</code>.</p>" if len(b["apps"]) > 40 else ""))
    # findings
    h.append("<h2>All findings</h2><div class='wrap'><table><tr><th>Severity</th><th>Area</th><th>Subject</th><th>Detail</th><th>Next step</th><th>Owner</th></tr>")
    h += [f"<tr><td>{_pill(f.severity)}</td><td class='nw'>{e(f.area)}</td><td>{e(f.subject)}</td><td>{e(f.detail)}</td><td class='fix'>{e(f.fix)}</td>"
          f"<td class='nw'>{e(f.owner)}</td></tr>" for f in findings]
    h.append("</table></div><footer class='muted'>Generated by cutover from devices.csv, users.csv and settings.json. Fictional demo data unless you ran it "
             "on your own inventory.</footer></main></body></html>")
    return "".join(h)
