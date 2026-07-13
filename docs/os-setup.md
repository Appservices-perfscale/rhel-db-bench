# os-setup.yaml — Pin RHEL Compose, Distro-Sync, Reboot

Pins both **bench** and **client** hosts to a specific RHEL nightly compose,
performs a full `dnf distro-sync`, reboots into the new kernel, and writes
Ansible facts to the controller for later reporting.

Run this playbook **before** `setup.yaml` on fresh or reprovisioned hosts.
It is the first step that ensures both machines are running the exact RHEL
build you want to benchmark, eliminating OS-level variance between runs.

## Quick start

```bash
# Both hosts (bench + client):
ansible-playbook playbooks/os-setup.yaml -i inventory.ini -i inventory.local.ini

# Bench only:
ansible-playbook playbooks/os-setup.yaml -i inventory.ini -i inventory.local.ini --limit bench

# Client only:
ansible-playbook playbooks/os-setup.yaml -i inventory.ini -i inventory.local.ini --limit client
```

---

## Why this playbook exists

Competitive performance testing compares NOPM across RHEL releases. For those
comparisons to be valid, the OS on each host must be pinned to an **exact
compose** — not whatever the provisioning system happened to install:

```
  Without os-setup                    With os-setup
  ──────────────────                  ──────────────────
  Host provisioned with               Host provisioned with
  whatever RHEL is current             whatever RHEL is current
          │                                    │
          ▼                                    ▼
  setup.yaml runs on                   os-setup.yaml pins
  an unknown minor release             to RHEL 9.7 nightly 2025-05-12
          │                                    │
          ▼                                    ▼
  Benchmark results are                setup.yaml runs on
  tied to an uncontrolled OS           a known, reproducible OS
          │                                    │
          ▼                                    ▼
  ✗ Not comparable                     ✓ Comparable across runs
    across runs
```

The playbook also removes third-party packages (PostgreSQL, PGDG, PCP) that
the provisioning system may have installed from different repos. These would
conflict with `distro-sync` against the target compose and would be
reinstalled by `setup.yaml` anyway.

---

## What the playbook does

The playbook chooses the migration path from the **current** OS major on the host
vs the **requested** `os_prep_rhel_release_id`:

| Situation | Path |
|-----------|------|
| Same major (e.g. 9.4 → 9.7) | `dnf distro-sync` against rel-eng compose |
| Different major (e.g. 9.4 → 10.2) | **Foreman re-provision** (wipe + PXE reinstall via Badfish) |

Major upgrades do **not** use distro-sync across majors (unsupported on RHEL).

### Foreman rebuild (9 → 10)

When the current OS major differs from the requested `os_prep_rhel_release_id`,
the playbook uses the ScaleLab Foreman REST API to wipe and reinstall the host.
If Foreman does not offer the exact target minor (e.g. only `RHEL 10.0` while
you requested `10.2`), the playbook installs the **highest Foreman RHEL
{major}.x that is still at or below the target**, then **distro-syncs** to the
requested release on the eng compose mirror.

Example: `cpt-run.sh --rhel 10.2` on a RHEL 9.4 host → Foreman rebuild to
`RHEL 10.0` → `dnf distro-sync` to `10.2`.

This is more reliable than in-place upgrade since `setup.yaml` reinstalls
PostgreSQL, HammerDB, and PCP from scratch anyway.

Adapted from the [QUADS project Foreman client](https://github.com/quadsproject/quads/blob/latest/src/quads/tools/external/foreman.py).

1. **Resolve Foreman OS** — query Foreman operatingsystems and pick the best
   install title for the target major (e.g. `RHEL 10.0` when targeting `10.2`).
2. **Set OS on Foreman host record** — via the Foreman v2 REST API, set
   `operatingsystem_id`, `medium_id`, `ptable_id`, and `build=true`.
   The resolved OS title must exist in Foreman with at least one install medium
   and partition table configured.
3. **PXE boot via Badfish** — set next-boot to PXE (`-i config/idrac_interfaces.yml --boot-to-type foreman --pxe`)
   and power-cycle the host (`--power-cycle`) using its BMC/iDRAC address.
   The interfaces YAML maps Dell server models to Foreman PXE NIC order (from
   [badfish](https://github.com/quadsproject/badfish/blob/master/config/idrac_interfaces.yml)).
4. **Wait for rebuild** — poll password SSH until login works (default 5 min
   initial pause, then up to ~85 min of retries). Port 22 can open before
   kickstart finishes, so the playbook checks auth—not just the TCP port.
5. **Distro-sync when needed** — if the Foreman install version is below the
   target (e.g. `10.0` installed, `10.2` requested), run same-major
   `dnf distro-sync` against the eng compose.
6. **Verify target version** — `rpm -q redhat-release` must contain the
   target release id.

#### Prerequisites

- **Foreman + Badfish credentials**: auto-derived by `auto-schedule.yaml`
  from the QUADS assignment. Foreman username = cloud name (e.g. `cloud42`),
  password = `rdu2@<ticket_number>`. Badfish username = `quads`, same password.
  Written to `inventory.local.ini` `[remote:vars]` (gitignored).
  BMC address per host is auto-derived as `mgmt-<hostname>`.
  Foreman URL can be overridden via `foreman_url` in `quads_cfg.yaml`.
- **Badfish installed** on the controller: `pip install badfish`
  (included in `requirements.txt`).
- **Target OS in Foreman**: list available titles with
  `python3 scripts/foreman_resolve_os.py --url ... --user ... --password ... --release 10.2`
  or `curl -su "cloudXX:pass" https://foreman.rdu2.scalelab.redhat.com/api/operatingsystems | jq '.results[]|{title,id}'`
  Current ScaleLab titles often include `RHEL 9.4` and `RHEL 10.0`.

For same-major **RHEL 10.x** pinning after a Foreman rebuild, set a dated eng compose:

```ini
os_prep_rhel_compose_name=RHEL-10.2-20260408.1
```

(`latest-RHEL-10.2.0` does not exist on the eng mirror.)

### Distro-sync (same major)

```
  ┌───────────────────────────────────────────────────────────────────┐
  │                       remote host                                 │
  │                                                                   │
  │   1. Check skip flag (os_prep_enable) ──────┐                     │
  │                                              │ skip if false      │
  │   2. Validate required variables ◄───────────┘                    │
  │          │                                                        │
  │          ▼                                                        │
  │   3. Show target compose URL + expected release ID                │
  │          │                                                        │
  │          ▼                                                        │
  │   4. Remove all existing .repo files from /etc/yum.repos.d        │
  │          │                                                        │
  │          ▼                                                        │
  │   5. Template BaseOS + AppStream repos pointing at compose        │
  │          │                                                        │
  │          ▼                                                        │
  │   6. Pin DNF releasever to target minor (e.g. 9.7)               │
  │          │                                                        │
  │          ▼                                                        │
  │   7. dnf clean all                                                │
  │          │                                                        │
  │          ▼                                                        │
  │   8. Stop PostgreSQL + PCP services                               │
  │          │                                                        │
  │          ▼                                                        │
  │   9. Remove PostgreSQL, PGDG, PCP packages                       │
  │          │                                                        │
  │          ▼                                                        │
  │  10. dnf distro-sync --allowerasing                               │
  │          │                                                        │
  │          ▼                                                        │
  │  11. Assert redhat-release matches expected ID                    │
  │          │                                                        │
  │          ▼                                                        │
  │  12. Reboot (up to 30 min timeout)                                │
  │                                                                   │
  └───────────────────────────────────────────────────────────────────┘
```

#### Step-by-step details

| # | Task | Why |
|---|------|-----|
| 1 | **Check `os_prep_enable`** | Allows individual hosts to opt out of OS pinning (e.g. when the host is already on the right build). Defaults to `true`. |
| 2 | **Assert `os_prep_rhel_release_root` and `os_prep_rhel_release_id`** | Fails fast with a clear message if the operator forgot to set the compose URL or expected release ID in inventory. These are per-group (`[bench:vars]` / `[client:vars]`) so bench and client can target different RHEL builds. |
| 3 | **Debug message** | Prints the target compose URL and release ID to the console so the operator can verify what is about to happen before the irreversible steps. |
| 4 | **Remove all `.repo` files** | Wipes every repository definition installed by the provisioning system. This prevents `distro-sync` from pulling packages from mixed sources (e.g. Satellite, EPEL, stale composes). |
| 5 | **Template new repo file** | Installs `os-prep-rhel.repo` with BaseOS and AppStream repos pointing at the exact compose URL from inventory. Optionally includes CRB when `os_prep_include_crb=true`. GPG checking is disabled because nightly composes are unsigned. |
| 6 | **Pin `releasever`** | Writes the target minor version (e.g. `9.7`) to `/etc/dnf/vars/releasever`. Without this, DNF resolves `$releasever` from the currently installed `redhat-release` package, which may not match the target. |
| 7 | **Clean DNF metadata** | `dnf clean all` forces DNF to fetch fresh metadata from the newly configured repos. Stale cache from the old repos would cause resolution failures. |
| 8 | **Stop services** | Stops PostgreSQL and PCP (`pmcd`) before package removal. Services with open files on packages being replaced would fail the transaction. |
| 9 | **Remove PostgreSQL, PGDG, PCP packages** | The provisioning system may have installed these from different repos (e.g. PGDG, EPEL). They would cause `distro-sync` conflicts because the target compose only contains base RHEL packages. `setup.yaml` reinstalls them afterward. Controlled by `os_prep_remove_provisioned_packages` (default `true`). |
| 10 | **`distro-sync --allowerasing`** | Synchronises every installed package to the version in the target compose. `--allowerasing` lets DNF replace packages whose names changed between minors (e.g. `libfoo` → `libfoo2`). `subscription-manager` plugin is disabled because the hosts use direct compose URLs, not CDN entitlements. |
| 11 | **Assert `redhat-release`** | Verifies that the installed `redhat-release` RPM contains the expected release ID (e.g. `9.7`). Catches compose URL mismatches or partial sync failures before rebooting. |
| 12 | **Reboot** | Boots into the kernel that came with the synced compose. The timeout defaults to 1800 seconds (30 minutes) via `os_prep_reboot_timeout`, because bare-metal hosts with large memory can take several minutes to POST. |

---

### Play 2 — Collect facts after reboot

```
  ┌──────────────────────────────────────────────────────┐
  │                    remote host                        │
  │                                                       │
  │   1. Check skip flag (os_prep_enable)                 │
  │          │                                            │
  │          ▼                                            │
  │   2. Assert redhat-release after reboot               │
  │          │                                            │
  │          ▼                                            │
  │   3. Assert Ansible sees correct RHEL major.minor     │
  │          │                                            │
  │          ▼                                            │
  │   4. Print kernel + distribution version              │
  │          │                                            │
  │          ▼                                            │
  │   5. Write facts.json to controller                   │
  │                                                       │
  └──────────────────────────────────────────────────────┘
```

| # | Task | Why |
|---|------|-----|
| 1 | **Check `os_prep_enable`** | Same skip logic as Play 1. |
| 2 | **Assert `redhat-release`** | Re-checks after reboot. A kernel mismatch or failed `grub2-set-default` could cause the host to boot into an older kernel with a stale `redhat-release`. |
| 3 | **Assert Ansible facts** | Verifies that `ansible_distribution_version` (gathered fresh after reboot) matches the target. This catches edge cases where `redhat-release` was updated but the running kernel disagrees. |
| 4 | **Debug message** | Prints kernel version and distribution for the operator's log. |
| 5 | **Write `facts.json`** | Dumps the full `ansible_facts` dictionary to `<results>/os-prep/<hostname>/facts.json` on the controller. Downstream playbooks and reporting scripts use this file to record which OS build was benchmarked. The output path is `perf_results_path/os-prep/` if set, otherwise `results/os-prep/`. |

---

## Key inventory variables

Per-group variables live in `inventory.ini` under `[bench:vars]` and
`[client:vars]`. Shared defaults go under `[remote:vars]`.

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `os_prep_rhel_release_mirror` | no* | `https://download.eng.pnq.redhat.com` | Base mirror; compose prefix is derived as `rhel-<major>/rel-eng/RHEL-<major>` from each host's `os_prep_rhel_release_id` (bench `10.2` → rhel-10; client `9.4` → rhel-9). |
| `os_prep_rhel_release_root_prefix` | no | — | Optional fixed prefix; overrides auto derivation for that host/group. |
| `os_prep_rhel_release_root_override` | no | — | Full compose root URL (dated build); skips prefix + `latest-RHEL-*` derivation. |
| `os_prep_rhel_release_id` | **yes** | — | Expected minor release ID (e.g. `9.7`, `10.2`). Used for assertion checks and URL derivation. |
| `os_prep_rhel_arch` | no | `x86_64` | Architecture subdirectory in the compose tree. |
| `os_prep_enable` | no | `true` | Set `false` to skip OS prep for a host. |
| `os_prep_include_crb` | no | `false` | Include the CRB (CodeReady Builder) repository in the generated repo file. |
| `os_prep_reboot_timeout` | no | `1800` | Seconds to wait for the host to come back after reboot. |
| `os_prep_stop_services` | no | `[postgresql-<major>, pmcd]` | List of systemd services to stop before `distro-sync`. |
| `os_prep_remove_provisioned_packages` | no | `true` | Remove PostgreSQL, PGDG, and PCP packages before sync. |
| `os_prep_foreman_os_title` | no | `RHEL <release_id>` | Target OS title as registered in Foreman. |
| `os_prep_foreman_host_fqdn` | no | `ansible_host` | Host FQDN as registered in Foreman. |
| `os_prep_foreman_rebuild_timeout` | no | `5400` | Total seconds budget for Foreman rebuild SSH wait (delay + retries). |
| `os_prep_foreman_rebuild_delay` | no | `300` | Seconds before first SSH auth attempt after PXE boot. |
| `os_prep_foreman_rebuild_ssh_retries` | no | `85` | Password SSH attempts after the initial pause. |
| `os_prep_foreman_rebuild_ssh_retry_delay` | no | `60` | Seconds between SSH auth attempts. |
| `os_prep_badfish_interfaces_yaml` | no | `config/idrac_interfaces.yml` | Badfish NIC boot-order map for `--boot-to-type foreman`. |
| `perf_results_path` | no | `results/` | Base directory for `facts.json` output on the controller. |

Foreman and Badfish **credentials** are auto-derived from the QUADS
assignment by `auto-schedule.yaml`: Foreman username = cloud name
(e.g. `cloud42`), password = `rdu2@<ticket_number>`. Badfish username
is always `quads` with the same password. These are written as
`os_prep_foreman_*` / `os_prep_badfish_*` into `inventory.local.ini`
`[remote:vars]` (gitignored). Per-host `os_prep_badfish_host` is
auto-derived as `mgmt-<hostname>`. Override `foreman_url` in
`quads_cfg.yaml` if needed.

---

## When to run it

```
  ┌─────────────────┐     ┌──────────────┐     ┌──────────────┐
  │ auto-schedule    │────▶│ os-setup     │────▶│ setup.yaml   │──▶ ...
  │ .yaml (optional) │     │ .yaml        │     │              │
  └─────────────────┘     └──────────────┘     └──────────────┘
   Reserve hosts           Pin RHEL build       Install PostgreSQL,
                           + reboot             HammerDB, PCP
```

- **Fresh hosts**: always run `os-setup.yaml` before `setup.yaml`.
- **Changing RHEL build**: run `cpt-run.sh` with a new `--rhel` (e.g. `10.2` on bench while client stays on `9.4`).
  Compose URLs are picked automatically from the major version (`rhel-9` vs `rhel-10`).
  Re-run `setup.yaml` after a major change ( `cpt-run.sh` does this unless `--skip-setup`).
- **Already on the correct build**: set `os_prep_enable: false` in inventory
  or simply skip the playbook.
