"""Command line: `demo` builds everything from the fictional carve-out, `plan` runs on your own inventory export."""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from . import report, simulate
from .checks import run_all
from .model import load, parse_date


def run(data: Path, out: Path, title: str, as_of: date | None = None) -> dict:
    d = load(data, as_of)
    findings = run_all(d)
    b = report.build(d, findings)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.html").write_text(report.render_html(d, findings, title, b), encoding="utf-8")
    (out / "cutover-plan.md").write_text(report.plan_md(d, findings, b), encoding="utf-8")
    report.write_waves(b, out / "waves.csv")
    report.write_devices(b, out / "device-readiness.csv")
    report.write_users(b, out / "user-readiness.csv")
    report.write_apps(b, out / "apps.csv")
    report.write_blockers(b, out / "blockers.csv")
    report.write_actions(findings, out / "actions.csv")
    report.write_findings(findings, out / "findings.csv")
    return report.summary(d, findings, b)


def _date(value: str) -> date:
    d = parse_date(value)
    if not d:
        raise argparse.ArgumentTypeError(f"not a date (YYYY-MM-DD): {value}")
    return d


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cutover", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="write the fictional carve-out to sample_data/ and build the outputs")
    d.add_argument("--data", type=Path, default=Path("sample_data"))
    d.add_argument("--out", type=Path, default=Path("output"))
    r = sub.add_parser("plan", help="build the outputs from your own devices.csv, users.csv and settings.json")
    r.add_argument("data", type=Path, help="folder with devices.csv, users.csv and settings.json")
    r.add_argument("--out", type=Path, default=Path("output"))
    r.add_argument("--title", default="Tenant separation · cutover readiness")
    r.add_argument("--as-of", type=_date, help="inventory date, YYYY-MM-DD (default: settings.json, or the latest last-seen date)")
    a = p.parse_args(argv)
    if a.cmd == "demo":
        simulate.write(a.data)
        sm = run(a.data, a.out, "Project Lighthouse · Tailspin Mobility carve-out · cutover readiness")
    else:
        try:
            sm = run(a.data, a.out, a.title, a.as_of)
        except (FileNotFoundError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    print(f"{sm['devices']} devices ({sm['dev_ready']} ready, {sm['dev_actions']} with actions, {sm['dev_blocked']} blocked, {sm['dev_exclude']} excluded) · "
          f"{sm['users']} users ({sm['usr_blocked']} blocked) · waves: {sm['go']} go, {sm['go_actions']} go with actions, {sm['nogo']} no-go · "
          f"{sm['unassigned']} not in a wave · {sm['critical']} critical, {sm['warning']} warnings")
    print(f"outputs in {a.out}/: report.html, cutover-plan.md, waves.csv, device-readiness.csv, user-readiness.csv, apps.csv, blockers.csv, actions.csv, findings.csv")
    return 0
