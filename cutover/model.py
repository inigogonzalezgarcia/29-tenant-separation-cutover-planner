"""Input model: a device inventory (CSV), a user list (CSV) and the programme settings (JSON).

The CSVs can come from Intune, MECM, Autopilot and Entra ID exports (the PowerShell script in scripts/ builds them),
or from any CMDB: columns are matched by name through a list of aliases, yes/no values are normalised, dates are
accepted in ISO and in the usual European formats. Anything that is not understood is left empty rather than guessed.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

DEVICE_ALIASES = {
    "name": ["name", "device name", "hostname", "computer name", "devicename", "nombre", "equipo"],
    "serial": ["serial", "serial number", "serialnumber", "numero de serie"],
    "user": ["user", "primary user", "user principal name", "upn", "userprincipalname", "primary user upn", "usuario"],
    "site": ["site", "location", "office", "sede", "oficina", "ubicacion"],
    "model": ["model", "device model", "modelo"],
    "os_build": ["os build", "os version", "osversion", "build", "windows build", "version del so"],
    "tpm": ["tpm", "tpm 2.0", "tpm version", "tpm20"],
    "secure_boot": ["secure boot", "secureboot", "uefi secure boot", "arranque seguro"],
    "ram_gb": ["ram gb", "ram", "memory gb", "physical memory gb", "memoria gb"],
    "disk_free_gb": ["disk free gb", "free disk gb", "free space gb", "disco libre gb"],
    "join_type": ["join type", "jointype", "trust type", "device trust type", "join", "tipo de union"],
    "management": ["management", "managed by", "management agent", "mdm", "managementagent", "gestion"],
    "autopilot": ["autopilot", "autopilot registered", "autopilot enrolled", "hardware hash uploaded", "autopilot profile"],
    "bitlocker_key": ["bitlocker key", "bitlocker key escrowed", "recovery key escrowed", "bitlocker recovery key", "clave bitlocker"],
    "compliance": ["compliance", "compliance state", "compliant", "cumplimiento"],
    "last_seen": ["last seen", "last sync", "last check-in", "last contact", "lastsyncdatetime", "ultima conexion"],
    "local_admins": ["local admins", "local administrators", "administrators", "admins locales"],
    "apps": ["apps", "applications", "installed apps", "software", "aplicaciones"],
    "vip": ["vip", "vip user", "usuario vip"],
    "kind": ["kind", "device type", "type", "device category", "tipo"],
}
USER_ALIASES = {
    "upn": ["upn", "user principal name", "userprincipalname", "user", "email", "mail", "usuario"],
    "name": ["name", "display name", "displayname", "nombre"],
    "department": ["department", "departamento", "dept"],
    "site": ["site", "office", "location", "office location", "sede", "oficina"],
    "manager": ["manager", "manager upn", "responsable"],
    "mailbox_gb": ["mailbox gb", "mailbox size gb", "mailbox size", "total item size gb", "buzon gb"],
    "onedrive_gb": ["onedrive gb", "onedrive size gb", "storage used gb", "onedrive"],
    "licences": ["licences", "licenses", "assigned licenses", "skus", "licencias"],
    "mfa": ["mfa", "mfa registered", "mfa capable", "strong auth", "mfa registrado"],
    "shared_mailboxes": ["shared mailboxes", "shared mailbox", "owns shared mailboxes", "buzones compartidos"],
    "last_signin": ["last signin", "last sign-in", "last sign in", "lastsignindatetime", "ultimo inicio de sesion"],
    "vip": ["vip", "usuario vip"],
    "leaving": ["leaving", "leaver", "offboarding", "baja"],
    "account_type": ["account type", "type", "user type", "tipo de cuenta"],
}
YES = {"yes", "y", "true", "1", "si", "sí", "x", "registered", "enabled", "escrowed", "compliant", "present"}
NO = {"no", "n", "false", "0", "", "none", "not registered", "noncompliant", "not compliant", "missing", "unknown", "-"}
JOIN_MAP = {"hybrid": "hybrid", "hybrid azure ad joined": "hybrid", "hybrid entra joined": "hybrid", "hybridjoined": "hybrid",
            "entra": "entra", "azure ad joined": "entra", "entra joined": "entra", "azureadjoined": "entra", "aadj": "entra",
            "domain": "domain", "domain joined": "domain", "on-premises": "domain", "ad joined": "domain", "domainjoined": "domain",
            "workgroup": "workgroup", "none": "workgroup", "registered": "registered", "azure ad registered": "registered"}
MGMT_MAP = {"intune": "intune", "mdm": "intune", "mdm/intune": "intune", "co managed": "comanaged", "comanaged": "comanaged", "comanagement": "comanaged",
            "configmgr": "mecm", "sccm": "mecm", "mecm": "mecm", "configurationmanagerclient": "mecm", "configuration manager": "mecm", "configmgr client": "mecm", "none": "none", "": "none",
            "unmanaged": "none"}
KIND_MAP = {"laptop": "laptop", "notebook": "laptop", "portatil": "laptop", "desktop": "desktop", "sobremesa": "desktop",
            "workstation": "desktop", "kiosk": "kiosk", "shared": "shared", "shared device": "shared", "meeting room": "shared",
            "tablet": "laptop", "surface": "laptop", "virtual": "virtual", "vm": "virtual"}
DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d",
                "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%d-%m-%Y %H:%M", "%d-%m-%Y", "%d.%m.%Y")


def _norm(s: str) -> str:
    s = s.strip().lower().replace("_", " ").replace("-", " ")
    s = re.sub(r"[()\[\]]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def yes(v: str) -> bool:
    return _norm(v) in YES


def parse_date(v: str) -> date | None:
    v = (v or "").strip()
    if not v:
        return None
    v = re.sub(r"\.\d+", "", v).replace("+0000", "").replace("+00:00", "").strip()
    v = re.sub(r"[+-]\d{2}:?\d{2}$", "", v).strip()
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(v, f).date()
        except ValueError:
            pass
    return None


def num(v: str, default: float = 0.0) -> float:
    v = (v or "").strip().replace(",", ".")
    m = re.search(r"-?\d+(\.\d+)?", v)
    return float(m.group()) if m else default


def build_number(v: str) -> int:
    """'10.0.22631.4317' -> 22631; '22H2' and friends are mapped; anything else -> 0."""
    v = (v or "").strip().lower()
    names = {"21h2": 22000, "22h2": 22621, "23h2": 22631, "24h2": 26100, "25h2": 26200, "win10 22h2": 19045, "1809": 17763,
             "2004": 19041, "20h2": 19042, "21h1": 19043}
    if v in names:
        return names[v]
    m = re.search(r"\b(\d{2}h[12])\b", v)
    if m and m.group(1) in names:
        return names[m.group(1)]
    parts = re.findall(r"\d+", v)
    for p in parts:
        n = int(p)
        if 10000 <= n < 100000:
            return n
    return 0


def _split(v: str) -> list[str]:
    return [x.strip() for x in re.split(r"[;|]", v or "") if x.strip()]


def _headers(fieldnames: list[str], aliases: dict) -> dict[str, str]:
    """canonical -> actual header, first alias match wins."""
    normed = {_norm(h): h for h in fieldnames if h}
    out = {}
    for canon, names in aliases.items():
        for a in names:
            if a in normed:
                out[canon] = normed[a]
                break
    return out


def _read_csv(path: Path) -> tuple[list[dict], list[str]]:
    raw = path.read_text(encoding="utf-8-sig")
    sample = raw[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.DictReader(raw.splitlines(), dialect=dialect))
    return rows, list(rows[0].keys()) if rows else []


@dataclass
class Device:
    name: str
    user: str = ""
    serial: str = ""
    site: str = ""
    model: str = ""
    os_build: int = 0
    tpm: bool = False
    secure_boot: bool = False
    ram_gb: float = 0.0
    disk_free_gb: float = 0.0
    join_type: str = "hybrid"
    management: str = "intune"
    autopilot: bool = False
    bitlocker_key: bool = False
    compliant: bool = True
    last_seen: date | None = None
    local_admins: list[str] = field(default_factory=list)
    apps: list[str] = field(default_factory=list)
    vip: bool = False
    kind: str = "laptop"
    wave: str = ""


@dataclass
class User:
    upn: str
    name: str = ""
    department: str = ""
    site: str = ""
    manager: str = ""
    mailbox_gb: float = 0.0
    onedrive_gb: float = 0.0
    licences: list[str] = field(default_factory=list)
    mfa: bool = False
    shared_mailboxes: list[str] = field(default_factory=list)
    last_signin: date | None = None
    vip: bool = False
    leaving: bool = False
    account_type: str = "member"
    wave: str = ""


@dataclass
class Wave:
    name: str
    date: date
    sites: list[str] = field(default_factory=list)
    departments: list[str] = field(default_factory=list)
    days: int = 1                 # length of the cutover window in working days
    techs: int = 2                # onsite technicians for the window
    pilot: bool = False


@dataclass
class Settings:
    programme: str = "Tenant separation"
    source_tenant: str = "source.onmicrosoft.com"
    target_tenant: str = "target.onmicrosoft.com"
    target_domain: str = ""
    as_of: date | None = None
    waves: list[Wave] = field(default_factory=list)
    min_os_build: int = 22631          # Windows 11 23H2
    min_ram_gb: float = 8.0
    min_disk_free_gb: float = 20.0
    last_seen_max_days: int = 14
    dormant_user_days: int = 90
    devices_per_tech_day: int = 25
    mailbox_max_gb: float = 50.0       # above this a mailbox needs pre-staging before the wave
    onedrive_max_gb: float = 100.0
    migration_gb_per_hour: float = 40.0
    window_hours: float = 36.0         # Friday evening to Sunday morning
    freeze: list[tuple[date, date]] = field(default_factory=list)
    app_catalog: dict[str, str] = field(default_factory=dict)       # app -> "approved" | "replace: X" | "blocked"
    licence_map: dict[str, str] = field(default_factory=dict)       # source SKU -> target SKU ("" = not provided)
    prerequisites: dict[str, bool] = field(default_factory=dict)    # target tenant readiness checklist
    blocked_share_max: float = 10.0    # % of blocked devices above which a wave is no-go
    currency: str = "€"


def settings_from_dict(d: dict) -> Settings:
    s = Settings()
    for k in ("programme", "source_tenant", "target_tenant", "target_domain", "currency"):
        if k in d:
            setattr(s, k, str(d[k]))
    for k in ("min_os_build", "last_seen_max_days", "dormant_user_days", "devices_per_tech_day"):
        if k in d:
            setattr(s, k, int(d[k]))
    for k in ("min_ram_gb", "min_disk_free_gb", "mailbox_max_gb", "onedrive_max_gb", "migration_gb_per_hour", "window_hours",
              "blocked_share_max"):
        if k in d:
            setattr(s, k, float(d[k]))
    if d.get("as_of"):
        s.as_of = parse_date(str(d["as_of"]))
    for w in d.get("waves", []):
        s.waves.append(Wave(name=str(w["name"]), date=parse_date(str(w["date"])) or date.today(), sites=list(w.get("sites", [])),
                            departments=list(w.get("departments", [])), days=int(w.get("days", 1)), techs=int(w.get("techs", 2)),
                            pilot=bool(w.get("pilot", False))))
    for f in d.get("freeze", []):
        a, b = parse_date(str(f.get("from", ""))), parse_date(str(f.get("to", "")))
        if a and b:
            s.freeze.append((a, b))
    s.app_catalog = {str(k).strip().lower(): str(v) for k, v in d.get("app_catalog", {}).items()}
    s.licence_map = {str(k): str(v) for k, v in d.get("licence_map", {}).items()}
    s.prerequisites = {str(k): bool(v) for k, v in d.get("prerequisites", {}).items()}
    return s


def load_devices(path: Path) -> list[Device]:
    rows, headers = _read_csv(path)
    h = _headers(headers, DEVICE_ALIASES)
    if "name" not in h:
        raise ValueError(f"{path.name}: no device name column (tried {', '.join(DEVICE_ALIASES['name'])})")
    g = lambda r, k: (r.get(h[k]) or "").strip() if k in h else ""  # noqa: E731
    out = []
    for r in rows:
        if not g(r, "name"):
            continue
        out.append(Device(
            name=g(r, "name").upper(), user=g(r, "user").lower(), serial=g(r, "serial"), site=g(r, "site"), model=g(r, "model"),
            os_build=build_number(g(r, "os_build")),
            tpm=(yes(g(r, "tpm")) or _norm(g(r, "tpm")) in {"2.0", "2", "v2"}) if "tpm" in h else True,
            secure_boot=yes(g(r, "secure_boot")) if "secure_boot" in h else True, ram_gb=num(g(r, "ram_gb")), disk_free_gb=num(g(r, "disk_free_gb")),
            join_type=JOIN_MAP.get(_norm(g(r, "join_type")), "hybrid" if not g(r, "join_type") else "domain"),
            management=MGMT_MAP.get(_norm(g(r, "management")), "none") if "management" in h else "intune", autopilot=yes(g(r, "autopilot")),
            bitlocker_key=yes(g(r, "bitlocker_key")), compliant=(_norm(g(r, "compliance")) not in NO - {""}) if "compliance" in h else True,
            last_seen=parse_date(g(r, "last_seen")), local_admins=_split(g(r, "local_admins")),
            apps=[a for a in _split(g(r, "apps"))], vip=yes(g(r, "vip")),
            kind=KIND_MAP.get(_norm(g(r, "kind")), "laptop")))
    return out


def load_users(path: Path) -> list[User]:
    rows, headers = _read_csv(path)
    h = _headers(headers, USER_ALIASES)
    if "upn" not in h:
        raise ValueError(f"{path.name}: no UPN column (tried {', '.join(USER_ALIASES['upn'])})")
    g = lambda r, k: (r.get(h[k]) or "").strip() if k in h else ""  # noqa: E731
    out = []
    for r in rows:
        if not g(r, "upn"):
            continue
        out.append(User(
            upn=g(r, "upn").lower(), name=g(r, "name"), department=g(r, "department"), site=g(r, "site"), manager=g(r, "manager").lower(),
            mailbox_gb=num(g(r, "mailbox_gb")), onedrive_gb=num(g(r, "onedrive_gb")), licences=_split(g(r, "licences")),
            mfa=yes(g(r, "mfa")), shared_mailboxes=[m.lower() for m in _split(g(r, "shared_mailboxes"))],
            last_signin=parse_date(g(r, "last_signin")), vip=yes(g(r, "vip")), leaving=yes(g(r, "leaving")),
            account_type=_norm(g(r, "account_type")) or "member"))
    return out


@dataclass
class Data:
    devices: list[Device]
    users: list[User]
    settings: Settings
    as_of: date

    def __post_init__(self):
        self.by_upn = {u.upn: u for u in self.users}
        self.devices_of = {}
        for d in self.devices:
            self.devices_of.setdefault(d.user, []).append(d)


def load(folder: Path, as_of: date | None = None) -> Data:
    folder = Path(folder)
    dev_path, usr_path, set_path = folder / "devices.csv", folder / "users.csv", folder / "settings.json"
    if not dev_path.exists():
        raise FileNotFoundError(f"{dev_path} not found")
    settings = settings_from_dict(json.loads(set_path.read_text(encoding="utf-8"))) if set_path.exists() else Settings()
    devices = load_devices(dev_path)
    users = load_users(usr_path) if usr_path.exists() else []
    when = as_of or settings.as_of or max([d.last_seen for d in devices if d.last_seen] or [date.today()])
    return Data(devices, users, settings, when)
