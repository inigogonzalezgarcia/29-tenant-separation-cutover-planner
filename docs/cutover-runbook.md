# Cutover runbook

How to use the planner's outputs across a tenant separation (carve-out) or a tenant-to-tenant migration, wave by wave. The planner does not touch either tenant; it reads exports and tells you what is not ready.

## The cadence

| When | What | Output used |
|---|---|---|
| Programme start | Agree the wave design (sites, departments, dates, techs), the freeze windows, the app catalog and the licence map. Write them in `settings.json`. | `cutover-plan.md` → Waves |
| Weekly | Re-export the inventories, re-run the planner, take the findings to the workstream leads. The counts should fall every week. | `report.html` → Fix first, `actions.csv` |
| T-7 per wave | Wave readiness review (go / no-go). Every item in `blockers.csv` for the wave has an owner and a date, or the item leaves the wave. | `waves.csv`, `blockers.csv` |
| T-2 | Pre-staged mailboxes synced, hardware hashes confirmed in the target, Autopilot profiles assigned, communications sent. | `user-readiness.csv` (big mailboxes), `device-readiness.csv` |
| Day | Follow "Cutover day, in order" in `cutover-plan.md`. | `cutover-plan.md` |
| T+1 | Hypercare on site, blockers list closed, lessons into the next wave. | `blockers.csv` (cleared column) |

## Reading the verdicts

**Devices**

- *Ready*: nothing to do before the wave.
- *Ready with actions*: can be migrated in the wave, but something has to happen on the day or just before (OS below the baseline, low disk, domain-only join, non-compliant, local admins, kiosk procedure, apps undecided).
- *Blocked*: do not put it in a wave until fixed. The four blockers are deliberate: no Autopilot hash (the reset lands in OOBE with no profile), no BitLocker key escrowed (a failed reset loses the disk), not Windows 11 capable (the target image will not install) and not seen for weeks (probably retired, the wave count is wrong).
- *Exclude*: the user is leaving; collect the device.

**Users**

- *Blocked*: no MFA method (Conditional Access in the target will stop them on day 1), mailbox over the pre-staging threshold (start the cross-tenant move a week early), or a licence with no target SKU (nobody has bought it yet).
- *With actions*: dormant (confirm with the manager), big OneDrive, shared mailbox split across waves, VIP slot.

**Waves**

- *Go*: no blockers, within capacity, mail fits the window, not in a freeze.
- *Go with actions*: the same, but some devices or users in the wave are still blocked. Fix them or move them to the next wave; the wave itself is sound.
- *No-go*: one of the structural reasons applies: over capacity, too many blocked devices, mail does not fit the window, a freeze, or (pilot) target prerequisites incomplete. The reason tells you what to change: more technicians or days, a split wave, a longer window, a different date, or finishing the target build.

## The fixes most waves need

| Finding | Who | Typical fix |
|---|---|---|
| Devices not in Autopilot | Endpoint engineering | Export the hardware hashes from the source Intune (or `Get-WindowsAutopilotInfo`) and import them in the target with the right group tag; wait for the profile to assign |
| BitLocker keys not escrowed | Endpoint engineering | Intune policy "Save BitLocker recovery information to Entra ID" on, or `manage-bde -protectors -adbackup C:` through a remediation script; verify in the device's recovery keys blade |
| Hardware not Windows 11 capable | Asset management | Replace (order lead time!) or document an exemption and a Windows 10 ESU path in the target; keep them out of waves until decided |
| Devices not seen for weeks | Site IT | Confirm with the site, retire the rest in the source Intune so the counts are real |
| Users without MFA | Identity | Registration campaign with a deadline; Temporary Access Pass for the day itself |
| Licences without a target mapping | Licensing | Buy the SKU in the target or decide the replacement before the first wave that needs it |
| Applications without a decision | Application owners | Catalog every app seen on more than a handful of devices: approved, replace with X, or blocked |
| Shared mailboxes split across waves | Messaging | Move the shared mailbox with the last wave of its members, or give early movers cross-tenant access for the gap |
| Over capacity | Programme | More technicians, more days, or split the wave; `days_needed` in `waves.csv` is the honest number |
| Mail over the window | Messaging | Pre-stage the big mailboxes a week before, or lengthen the window; the final sync only moves the delta |

## Governance

Each wave's go / no-go goes through the change board as a standard change with the planner's `cutover-plan.md` attached; the freeze windows in `settings.json` should be the ones the board publishes. Keep the weekly runs (the output folder, dated) so the trend is visible and the decisions are traceable.
