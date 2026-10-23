"""A fictional carve-out: Tailspin Mobility leaves the Wingtip Holdings tenant. Four sites, five waves, 1,180 devices,
1,050 users, and the problems a real readiness review finds, placed on purpose. Deterministic (seeded)."""
from __future__ import annotations

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

AS_OF = date(2026, 10, 23)
SOURCE, TARGET = "wingtipholdings.onmicrosoft.com", "tailspinmobility.onmicrosoft.com"
DOMAIN = "tailspinmobility.com"
SITES = {"Madrid": 520, "Lisbon": 210, "Milan": 290, "Warsaw": 160}
DEPTS = ["Finance", "Sales", "Operations", "Engineering", "HR", "IT", "Legal", "Marketing", "Customer Service", "Plant"]
MODELS_OK = ["Latitude 5540", "Latitude 7440", "EliteBook 840 G10", "ThinkPad T14 Gen 4", "Surface Laptop 6", "OptiPlex 7010"]
MODELS_OLD = ["Latitude 5490", "OptiPlex 3060", "EliteDesk 800 G4", "ThinkCentre M720"]
APPS_APPROVED = ["Microsoft 365 Apps", "Microsoft Teams", "Adobe Acrobat Reader", "7-Zip", "Google Chrome", "Zoom", "Cisco Secure Client",
                 "Citrix Workspace", "Power BI Desktop", "SAP GUI", "Autodesk DWG TrueView", "Visio"]
APPS_REPLACE = {"Notepad++": "replace: Visual Studio Code", "VLC": "replace: Windows Media Player", "WinSCP": "replace: FileZilla (packaged)"}
APPS_UNKNOWN = ["PlantView HMI 4.2", "FleetTrack Desktop", "Sage 50", "ScanSnap Manager", "Legacy Label Printer Utility", "QuoteBuilder 2019", "TeamViewer"]
APPS_BLOCKED = ["Dropbox", "uTorrent"]
LICENCES = ["Microsoft 365 E3", "Microsoft 365 E5", "Visio Plan 2", "Project Plan 3", "Power BI Pro", "Teams Phone Standard"]
FIRST = ["laura", "marco", "ana", "pawel", "sofia", "joao", "elena", "tomasz", "giulia", "ines", "carlos", "agnieszka", "luca", "rita", "miguel",
         "katarzyna", "paolo", "beatriz", "david", "marta", "andrea", "nuno", "chiara", "jorge", "ola", "francesco", "teresa", "pedro", "sara", "hugo"]
LAST = ["prado", "rossi", "silva", "nowak", "garcia", "ferreira", "bianchi", "kowalski", "lopez", "costa", "ricci", "wisniewski", "martin",
        "santos", "russo", "lewandowski", "sanchez", "oliveira", "marino", "zielinski", "romero", "almeida", "greco", "szymanski", "navarro"]

SETTINGS = {
    "programme": "Project Lighthouse · Tailspin Mobility carve-out",
    "source_tenant": SOURCE, "target_tenant": TARGET, "target_domain": DOMAIN, "as_of": AS_OF.isoformat(),
    "min_os_build": 22631, "min_ram_gb": 8, "min_disk_free_gb": 20, "last_seen_max_days": 14, "dormant_user_days": 90,
    "devices_per_tech_day": 25, "mailbox_max_gb": 50, "onedrive_max_gb": 100, "migration_gb_per_hour": 50, "window_hours": 72,
    "blocked_share_max": 10,
    "waves": [
        {"name": "W0 Pilot IT", "date": "2026-11-09", "sites": ["Madrid"], "departments": ["IT"], "days": 2, "techs": 2, "pilot": True},
        {"name": "W1 Warsaw", "date": "2026-11-16", "sites": ["Warsaw"], "days": 2, "techs": 4},
        {"name": "W2 Lisbon", "date": "2026-11-23", "sites": ["Lisbon"], "days": 3, "techs": 2},
        {"name": "W3 Milan", "date": "2026-11-30", "sites": ["Milan"], "days": 4, "techs": 3},
        {"name": "W4 Madrid A", "date": "2026-12-14", "departments": ["Finance", "Sales", "Marketing", "Legal", "HR"], "days": 4, "techs": 4},
        {"name": "W5 Madrid B", "date": "2026-12-28", "sites": ["Madrid"], "days": 4, "techs": 4},
    ],
    "freeze": [{"from": "2026-12-23", "to": "2027-01-07", "why": "year-end close and holidays"}],
    "app_catalog": {**{a: "approved" for a in APPS_APPROVED}, **APPS_REPLACE, **{a: "blocked" for a in APPS_BLOCKED}},
    "licence_map": {"Microsoft 365 E3": "Microsoft 365 E3", "Microsoft 365 E5": "Microsoft 365 E5", "Power BI Pro": "Power BI Pro",
                    "Project Plan 3": "Project Plan 3", "Teams Phone Standard": "Teams Phone Standard", "Visio Plan 2": ""},
    "prerequisites": {"Target tenant created with verified domain tailspinmobility.com": True, "Autopilot deployment profile and ESP in target Intune": True,
                      "Compliance and configuration baselines in target Intune": True, "Conditional Access baseline in target (report-only passed)": True,
                      "Cross-tenant mailbox migration endpoint and org relationship": True, "MX and mail routing cutover plan signed off": True,
                      "Cross-tenant OneDrive migration tested with 10 users": True, "Service desk briefed and hypercare rota published": False},
}
DEV_FIELDS = ["Device name", "Serial number", "Primary user UPN", "Site", "Model", "OS version", "TPM 2.0", "Secure Boot", "RAM GB", "Free disk GB",
              "Join type", "Management agent", "Autopilot registered", "BitLocker key escrowed", "Compliance state", "Last sync", "Local admins",
              "Installed apps", "VIP", "Device type"]
USR_FIELDS = ["User principal name", "Display name", "Department", "Office location", "Manager", "Mailbox size GB", "OneDrive GB",
              "Assigned licenses", "MFA registered", "Shared mailboxes", "Last sign-in", "VIP", "Leaving", "Account type"]


def _pick(rng: random.Random, weighted: list[tuple[str, float]]) -> str:
    return rng.choices([w[0] for w in weighted], [w[1] for w in weighted])[0]


def build(seed: int = 29) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    users, devices, upns = [], [], set()
    managers: dict[tuple[str, str], str] = {}
    shared = {"finance-ap": ["Finance"], "sales-emea": ["Sales", "Marketing"], "hr-recruiting": ["HR"], "plant-maint-milan": ["Plant"],
              "customer-care": ["Customer Service"], "legal-contracts": ["Legal"], "ops-logistics": ["Operations", "Plant"], "it-helpdesk": ["IT"]}
    n = 0
    for site, count in SITES.items():
        for i in range(count):
            n += 1
            while True:
                upn = f"{rng.choice(FIRST)}.{rng.choice(LAST)}{'' if rng.random() < 0.7 else rng.randint(2, 9)}@{DOMAIN}"
                if upn not in upns:
                    upns.add(upn)
                    break
            if site == "Milan":
                dept = _pick(rng, [("Plant", 0.45), ("Operations", 0.2), ("Engineering", 0.15), ("Finance", 0.05), ("HR", 0.05), ("IT", 0.05), ("Sales", 0.05)])
            elif site == "Warsaw":
                dept = _pick(rng, [("Customer Service", 0.5), ("Operations", 0.2), ("IT", 0.1), ("Finance", 0.1), ("HR", 0.1)])
            else:
                dept = _pick(rng, [(x, 1.0) for x in DEPTS if x != "Plant"])
            key = (site, dept)
            if key not in managers:
                managers[key] = upn
            vip = dept in ("Finance", "Legal", "Sales") and rng.random() < 0.05 or (site == "Madrid" and i < 12)
            leaving = rng.random() < 0.009
            last_signin = AS_OF - timedelta(days=rng.randint(0, 6)) if rng.random() < 0.95 else AS_OF - timedelta(days=rng.randint(95, 240))
            mailbox = round(rng.lognormvariate(1.9, 0.8), 1)
            if dept in ("Legal", "Finance", "Sales") and rng.random() < 0.06:
                mailbox = round(rng.uniform(52, 115), 1)
            onedrive = round(rng.lognormvariate(2.5, 0.9), 1)
            lic = ["Microsoft 365 E5" if vip or dept in ("IT", "Legal") else "Microsoft 365 E3"]
            if dept in ("Engineering", "Operations") and rng.random() < 0.25:
                lic.append("Visio Plan 2")
            if dept in ("Finance", "Sales") and rng.random() < 0.3:
                lic.append("Power BI Pro")
            if rng.random() < 0.05:
                lic.append("Project Plan 3")
            if dept == "Customer Service":
                lic.append("Teams Phone Standard")
            sm = [m for m, ds in shared.items() if dept in ds and rng.random() < 0.35]
            users.append(dict(upn=upn, name=upn.split("@")[0].replace(".", " ").title(), dept=dept, site=site, manager=managers[key] if managers[key] != upn else "",
                              mailbox=mailbox, onedrive=onedrive, lic=lic, mfa=rng.random() > 0.046, shared=sm, last_signin=last_signin, vip=vip,
                              leaving=leaving, kind="member"))
            # device(s)
            old_hw = (site == "Milan" and dept == "Plant" and rng.random() < 0.33) or rng.random() < 0.015
            model = rng.choice(MODELS_OLD) if old_hw else rng.choice(MODELS_OK)
            desktop = model.startswith(("OptiPlex", "EliteDesk", "ThinkCentre"))
            build_ = rng.choice([26100, 22631, 22631, 22631]) if not old_hw else rng.choice([19045, 19045, 22621])
            join = "hybrid" if rng.random() < 0.9 else rng.choice(["entra", "domain"])
            mgmt = "comanaged" if join == "hybrid" and rng.random() < 0.8 else "intune" if join == "entra" else "mecm" if join == "domain" else "comanaged"
            last_seen = AS_OF - timedelta(days=rng.randint(0, 5)) if rng.random() < 0.9 else AS_OF - timedelta(days=rng.randint(15, 45)) if rng.random() < 0.85 \
                else AS_OF - timedelta(days=rng.randint(60, 200))
            apps = ["Microsoft 365 Apps", "Microsoft Teams", "Google Chrome", "Adobe Acrobat Reader", "Cisco Secure Client"]
            apps += rng.sample(["7-Zip", "Zoom", "Citrix Workspace", "Power BI Desktop", "SAP GUI", "Notepad++", "VLC", "WinSCP", "Visio"], rng.randint(1, 4))
            if dept == "Plant":
                apps += ["PlantView HMI 4.2"] if rng.random() < 0.6 else []
                apps += ["Legacy Label Printer Utility"] if rng.random() < 0.3 else []
            if dept == "Operations" and rng.random() < 0.3:
                apps.append("FleetTrack Desktop")
            if dept == "Finance" and site == "Lisbon" and rng.random() < 0.5:
                apps.append("Sage 50")
            if dept == "Sales" and rng.random() < 0.15:
                apps.append("QuoteBuilder 2019")
            if rng.random() < 0.04:
                apps.append(rng.choice(["TeamViewer", "ScanSnap Manager", "Dropbox", "uTorrent"]))
            devices.append(dict(name=f"{'TSM' if rng.random() < 0.8 else 'WH'}-{site[:3].upper()}-{n:05d}", serial=f"{rng.choice('ABCDEFGH')}{rng.randint(100000, 999999)}{rng.choice('XYZ')}",
                                user=upn, site=site, model=model, build=build_, tpm=not old_hw or rng.random() < 0.3, sb=not old_hw or rng.random() < 0.5,
                                ram=rng.choice([16, 16, 32]) if not old_hw else rng.choice([4, 8, 8]), free=round(rng.uniform(25, 300), 0) if rng.random() < 0.93 else round(rng.uniform(4, 19), 0),
                                join=join, mgmt=mgmt, ap=rng.random() > 0.025, bl=rng.random() > 0.012, comp=rng.random() > 0.05, seen=last_seen,
                                admins=[upn] if rng.random() < 0.06 else [], apps=sorted(set(apps)), vip=vip,
                                kind="desktop" if desktop else "laptop"))
    # kiosks and shared devices, no primary user, domain-joined
    for site, k in (("Milan", 14), ("Warsaw", 5), ("Madrid", 3)):
        for i in range(k):
            n += 1
            devices.append(dict(name=f"KIOSK-{site[:3].upper()}-{i + 1:02d}", serial=f"K{rng.randint(100000, 999999)}", user="", site=site, model="OptiPlex 3060",
                                build=19045, tpm=True, sb=True, ram=8, free=60, join="domain", mgmt="mecm", ap=False, bl=True, comp=True,
                                seen=AS_OF - timedelta(days=rng.randint(0, 3)), admins=[], apps=["Microsoft Teams", "Google Chrome", "PlantView HMI 4.2"],
                                vip=False, kind="kiosk"))
    # a handful of devices for an office not in any wave
    for i in range(6):
        n += 1
        devices.append(dict(name=f"TSM-ATH-{n:05d}", serial=f"A{rng.randint(100000, 999999)}", user="", site="Athens", model="Latitude 7440", build=22631,
                            tpm=True, sb=True, ram=16, free=120, join="entra", mgmt="intune", ap=True, bl=True, comp=True, seen=AS_OF - timedelta(days=2),
                            admins=[], apps=["Microsoft 365 Apps", "Microsoft Teams"], vip=False, kind="laptop"))
    # service accounts and a guest in users.csv
    users.append(dict(upn=f"svc-scanner@{DOMAIN}", name="svc-scanner", dept="IT", site="Madrid", manager="", mailbox=0.2, onedrive=0, lic=["Microsoft 365 E3"],
                      mfa=False, shared=[], last_signin=AS_OF, vip=False, leaving=False, kind="service"))
    users.append(dict(upn="auditor_contoso.com#ext#@" + SOURCE, name="External auditor", dept="Finance", site="Madrid", manager="", mailbox=0, onedrive=0, lic=[],
                      mfa=True, shared=[], last_signin=AS_OF - timedelta(days=12), vip=False, leaving=False, kind="guest"))
    return devices, users


def _d(v) -> str:
    return v.strftime("%Y-%m-%d %H:%M") if isinstance(v, date) else str(v)


def write(folder: Path, seed: int = 29) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    devices, users = build(seed)
    with open(folder / "devices.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(DEV_FIELDS)
        for x in devices:
            w.writerow([x["name"], x["serial"], x["user"], x["site"], x["model"], f"10.0.{x['build']}.{x['build'] % 997 + 1000}", "Yes" if x["tpm"] else "No",
                        "Enabled" if x["sb"] else "Disabled", x["ram"], int(x["free"]), {"hybrid": "Hybrid Azure AD joined", "entra": "Azure AD joined",
                        "domain": "Domain joined"}[x["join"]], {"comanaged": "Co-managed", "intune": "Intune", "mecm": "ConfigMgr"}[x["mgmt"]],
                        "Yes" if x["ap"] else "No", "Yes" if x["bl"] else "No", "Compliant" if x["comp"] else "Noncompliant", _d(x["seen"]),
                        "; ".join(x["admins"]), "; ".join(x["apps"]), "Yes" if x["vip"] else "No", x["kind"]])
    with open(folder / "users.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(USR_FIELDS)
        for u in users:
            w.writerow([u["upn"], u["name"], u["dept"], u["site"], u["manager"], u["mailbox"], u["onedrive"], "; ".join(u["lic"]), "Yes" if u["mfa"] else "No",
                        "; ".join(f"{m}@{DOMAIN}" for m in u["shared"]), _d(u["last_signin"]), "Yes" if u["vip"] else "No", "Yes" if u["leaving"] else "No", u["kind"]])
    (folder / "settings.json").write_text(json.dumps(SETTINGS, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return folder
