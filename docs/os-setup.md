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

The playbook contains two plays, both targeting `[remote]` (bench + client).
Hosts run sequentially (`serial: 1`) to avoid rebooting both machines at the
same time.

### Play 1 — Sync OS to requested RHEL build

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
| `os_prep_rhel_release_root` | **yes** | — | URL to the RHEL compose root (e.g. `http://download.eng.bos.redhat.com/rhel-9/nightly/RHEL-9/latest-RHEL-9.7.0/`). |
| `os_prep_rhel_release_id` | **yes** | — | Expected minor release ID (e.g. `9.7`). Used for assertion checks. |
| `os_prep_rhel_arch` | no | `x86_64` | Architecture subdirectory in the compose tree. |
| `os_prep_enable` | no | `true` | Set `false` to skip OS prep for a host. |
| `os_prep_include_crb` | no | `false` | Include the CRB (CodeReady Builder) repository in the generated repo file. |
| `os_prep_reboot_timeout` | no | `1800` | Seconds to wait for the host to come back after reboot. |
| `os_prep_stop_services` | no | `[postgresql-<major>, pmcd]` | List of systemd services to stop before `distro-sync`. |
| `os_prep_remove_provisioned_packages` | no | `true` | Remove PostgreSQL, PGDG, and PCP packages before sync. |
| `perf_results_path` | no | `results/` | Base directory for `facts.json` output on the controller. |

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
- **Changing RHEL build**: update `os_prep_rhel_release_root` and
  `os_prep_rhel_release_id` in inventory, then re-run `os-setup.yaml`
  followed by `setup.yaml`.
- **Already on the correct build**: set `os_prep_enable: false` in inventory
  or simply skip the playbook.
