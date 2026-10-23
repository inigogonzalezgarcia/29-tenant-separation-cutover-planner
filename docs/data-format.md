# Data format

The planner reads one folder with three files. Column names are matched case-insensitively, with the aliases listed below (Spanish headers included), so an Intune, Entra or Exchange export usually works without renaming. Values like `Yes/No`, `True/False`, `1/0`, `Enabled`, `Compliant` or `Registered` all count as yes.

## devices.csv

One row per Windows device in the source tenant. Minimum: a device name. Everything else improves the checks.

| Column (aliases) | Used for |
|---|---|
| `Device name` (name, hostname, computer name) | identity; upper-cased |
| `Serial number` | shown in the lists |
| `Primary user UPN` (user, upn, userprincipalname) | links the device to `users.csv`; empty for kiosks and shared devices |
| `Site` (location, office) | wave assignment when the device has no user |
| `Model` | shown in the lists |
| `OS version` (os build, build) | `10.0.22631.4317` → build 22631; `23H2`, `24H2` and friends are mapped. Below `min_os_build` is an action (the reset installs the target image) |
| `TPM 2.0`, `Secure Boot`, `RAM GB` | Windows 11 eligibility; a miss is a blocker (replace or exempt). A column that is absent counts as capable, so merge the hardware readiness export before trusting this check |
| `Free disk GB` | below `min_disk_free_gb` is an action |
| `Join type` (trust type) | `Hybrid Azure AD joined`, `Azure AD joined`, `Domain joined`, `Workgroup`, `Azure AD registered`. Domain-only, workgroup and registered devices need a full reset |
| `Management agent` (managed by, mdm) | `Intune`, `Co-managed`, `ConfigMgr`, `None` |
| `Autopilot registered` | no hardware hash in the target → blocker |
| `BitLocker key escrowed` (recovery key escrowed) | key not in Entra → blocker |
| `Compliance state` | non-compliant → action |
| `Last sync` (last seen, last check-in) | older than `last_seen_max_days` → action; older than 4× that → blocker (probably retired) |
| `Local admins` | listed; lost on reset → action |
| `Installed apps` | `;`-separated; checked against `app_catalog` |
| `VIP` | white-glove slot |
| `Device type` (kind) | `laptop`, `desktop`, `kiosk`, `shared`, `virtual`. Kiosk and shared devices get the shared-device procedure; virtual devices skip the hardware check |

## users.csv

One row per user in scope. Minimum: a UPN.

| Column (aliases) | Used for |
|---|---|
| `User principal name` (upn, email) | identity; lower-cased |
| `Display name` | shown in the lists |
| `Department`, `Office location` (site) | wave assignment |
| `Manager` (manager upn) | note when the manager moves in another wave |
| `Mailbox size GB` (mailbox size, total item size gb) | wave migration hours; over `mailbox_max_gb` → pre-stage (blocker until done) |
| `OneDrive GB` | over `onedrive_max_gb` → action |
| `Assigned licenses` (licences, skus) | `;`-separated; each SKU needs an entry in `licence_map` or the user is blocked |
| `MFA registered` | no method → blocker (Conditional Access in the target) |
| `Shared mailboxes` | `;`-separated; a shared mailbox whose members sit in several waves → action |
| `Last sign-in` | older than `dormant_user_days` → action |
| `VIP` | white-glove slot |
| `Leaving` (leaver, offboarding) | excluded from the migration, device collected |
| `Account type` | `member` (default), `guest`, `service`: non-members are noted for the identity workstream |

## settings.json

```json
{
  "programme": "Project Lighthouse",
  "source_tenant": "wingtipholdings.onmicrosoft.com",
  "target_tenant": "tailspinmobility.onmicrosoft.com",
  "target_domain": "tailspinmobility.com",
  "as_of": "2026-10-23",
  "min_os_build": 22631,
  "min_ram_gb": 8,
  "min_disk_free_gb": 20,
  "last_seen_max_days": 14,
  "dormant_user_days": 90,
  "devices_per_tech_day": 25,
  "mailbox_max_gb": 50,
  "onedrive_max_gb": 100,
  "migration_gb_per_hour": 50,
  "window_hours": 72,
  "blocked_share_max": 10,
  "waves": [
    {"name": "W0 Pilot IT", "date": "2026-11-09", "sites": ["Madrid"], "departments": ["IT"], "days": 2, "techs": 2, "pilot": true},
    {"name": "W1 Warsaw", "date": "2026-11-16", "sites": ["Warsaw"], "days": 2, "techs": 4}
  ],
  "freeze": [{"from": "2026-12-23", "to": "2027-01-07", "why": "year-end close and holidays"}],
  "app_catalog": {"Microsoft 365 Apps": "approved", "Sage 50": "replace: Dynamics 365 Business Central", "TeamViewer": "blocked"},
  "licence_map": {"Microsoft 365 E3": "Microsoft 365 E3", "Visio Plan 2": ""},
  "prerequisites": {"Target tenant created with verified domain": true, "Service desk briefed": false}
}
```

How the pieces work:

- **Waves.** A user lands in the first wave that matches. A wave with both `sites` and `departments` needs both; with only one list, that one. Devices follow their primary user; a device without a user goes to the first site-only wave for its site. Whatever matches nothing is listed under "not in a wave".
- **Capacity.** `techs × devices_per_tech_day × days`. More devices than that is a no-go; the plan says how many days the wave really needs.
- **Mailboxes.** The sum of mailbox sizes in the wave divided by `migration_gb_per_hour` must fit in `window_hours`. Mailboxes over `mailbox_max_gb` are flagged for pre-staging a week before (cross-tenant mailbox migration syncs incrementally, so the final cutover only moves the delta).
- **Freeze.** A wave whose window overlaps a freeze is a no-go.
- **Blocked share.** More than `blocked_share_max` % of the wave's devices blocked is a no-go: fix the blockers or move the devices out.
- **Pilot.** A wave with `"pilot": true` is a no-go until every prerequisite is `true`.
- **app_catalog.** `approved`, `replace: <target app>` or `blocked`. Apps seen on devices and missing from the catalog are "undecided", which is an action on the device and a critical finding for the application workstream once there are more than a few.
- **licence_map.** Source SKU → target SKU. An empty target means "not bought yet" and blocks every user holding that licence.

Dates accept `YYYY-MM-DD`, `YYYY-MM-DD HH:MM`, ISO with `T`/`Z`, and `DD/MM/YYYY`. Numbers accept a decimal comma.
