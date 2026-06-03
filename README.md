# DB-CPT-RHEL

**Database Competitive Performance Testing** — an Ansible-driven PostgreSQL
TPC-C benchmark framework for comparing RHEL releases on bare-metal hardware.


Two bare-metal hosts run on the **same rack** to eliminate network variability.
The **bench** host runs PostgreSQL (the system under test), and the **client**
host generates TPC-C load using HammerDB inside a Podman container. PCP
(Performance Co-Pilot) captures system metrics on both hosts throughout the
benchmark.

The primary metric is **NOPM** (New Orders Per Minute) — the TPC-C throughput
indicator — compared across RHEL releases to detect performance regressions or
improvements.

---

## Quick start

```bash
# 1. (Optional) Reserve ScaleLab hosts
ansible-playbook playbooks/auto-schedule.yaml -e "workload_name='DB-CPT RHEL 10'"

# 2. Pin RHEL compose on both hosts
ansible-playbook playbooks/os-setup.yaml -i inventory.ini -i inventory.local.ini

# 3. One-time provisioning (PostgreSQL, HammerDB, PCP, kernel tuning)
ansible-playbook playbooks/setup.yaml

# 4. Run a benchmark (full pipeline with master JSON)
ansible-playbook playbooks/site.yml

# 5. Reset between runs
ansible-playbook playbooks/cleanup.yaml

# 6. Run again
ansible-playbook playbooks/site.yml

# 7. Release ScaleLab hosts when finished
ansible-playbook playbooks/scalelab-cleanup.yaml
```

---

## Playbook workflow

```
  ┌──────────────────┐
  │ auto-schedule    │  Reserve ScaleLab hosts (optional)
  │     .yaml        │  Writes inventory.local.ini + scalelab_assignment.yml
  └────────┬─────────┘
           ▼
  ┌──────────────────┐
  │ os-setup.yaml    │  Pin RHEL compose, distro-sync, reboot
  └────────┬─────────┘
           ▼
  ┌──────────────────┐
  │ setup.yaml       │  Install PostgreSQL, HammerDB, PCP, kernel tuning
  │ (or setup_EL9)   │  (one-time)
  └────────┬─────────┘
           ▼
  ┌══════════════════╗
  ║ site.yml         ║  Benchmark + artifact assembly
  ║ (or test.yaml)   ║  Produces results/*.json + *.log
  ╚════════╤═════════╝
           ▼                    ◀── repeat as needed
  ┌──────────────────┐              with cleanup in between
  │ cleanup.yaml     │  Reset database, caches, PCP state
  └────────┬─────────┘
           ▼
  ┌──────────────────┐
  │ scalelab-cleanup │  Release ScaleLab hosts
  │     .yaml        │
  └──────────────────┘
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [docs/release-orchestrate.md](docs/release-orchestrate.md) | Automated CPT on new RHEL releases |
| [docs/auto-schedule.md](docs/auto-schedule.md) | Reserve ScaleLab hosts via QUADS API |
| [docs/os-setup.md](docs/os-setup.md) | Pin RHEL compose, distro-sync, reboot |
| [docs/setup.md](docs/setup.md) | One-time provisioning (bench + client) |
| [docs/setup_EL9.md](docs/setup_EL9.md) | RHEL 9.0 setup with PGDG workaround |
| [docs/test-logic.md](docs/test-logic.md) | Benchmark flow, PostgreSQL tuning rationale, PCP metrics |
| [docs/site.md](docs/site.md) | Full pipeline: test + master JSON assembly |
| [docs/cleanup.md](docs/cleanup.md) | Reset between benchmark runs |
| [docs/scalelab-cleanup.md](docs/scalelab-cleanup.md) | Release ScaleLab hosts after testing |

---

## Automated CPT on new RHEL releases

Run a single playbook to automatically detect a new RHEL GA, provision hosts,
benchmark, and clean up:

```bash
ansible-playbook playbooks/release-orchestrate.yaml
```

**How it works:**

1. `scripts/release-watcher.py` polls compose mirrors defined in
   `config/cpt-automation.yaml` for new GA build IDs.
2. If a new (untested) GA is found, it writes a bench-only inventory overlay
   to `inventory/generated/rhel-ga.ini` — the **client stays at RHEL 9.4**
   (from `inventory.ini` `[client:vars]`).
3. If the new GA is a different **major** version than the current host
   (e.g. RHEL 9 → 10), the orchestrator queries the QUADS `os_list` API to
   find a matching Foreman OS title and passes `ostype` + `wipe=true` to
   `auto-schedule.yaml` for Foreman reprovisioning.
4. After Foreman reinstall (or for same-major releases), `os-setup.yaml`
   runs `distro-sync` to pin the **exact** GA compose on bench. The client
   is always synced back to RHEL 9.4.
5. `setup.yaml` provisions PostgreSQL, HammerDB, PCP, and kernel tuning.
6. `run-matrix.yaml` runs the full benchmark sweep.
7. `state/last-tested.json` is updated so the same build is not re-tested.
8. ScaleLab hosts are released.

**Cron example** (run daily at 06:00 UTC):

```cron
0 6 * * * cd /path/to/DB-CPT-RHEL && ansible-playbook playbooks/release-orchestrate.yaml
```

**Manual override** (skip watcher, force a specific release):

```bash
ansible-playbook playbooks/release-orchestrate.yaml \
  -e "force_release_id=10.2" \
  -e "force_compose_root=https://download.eng.pnq.redhat.com/rhel-10/rel-eng/RHEL-10/latest-RHEL-10.2.0"
```

**Future-proof:** No hardcoded major version lists. The watcher parses the
major from compose targets, and dynamically queries `os_list` for a matching
Foreman title — RHEL 11, 12, etc. work without code changes.

---

## Project structure

```
DB-CPT-RHEL/
├── ansible.cfg                  Ansible configuration
├── inventory.ini                Main inventory (variables, host placeholders)
├── inventory.local.ini          Generated by auto-schedule (actual host IPs)
├── quads_cfg.yaml               QUADS API credentials and preferences
├── scalelab_assignment.yml      Assignment record (generated)
├── requirements.yml             Ansible Galaxy collection dependencies
│
├── config/
│   └── cpt-automation.yaml      Automation policy (mirror targets, client pin)
│
├── playbooks/
│   ├── auto-schedule.yaml       Reserve ScaleLab hosts (supports ostype for cross-major)
│   ├── release-orchestrate.yaml Automated CPT pipeline (watcher → benchmark → cleanup)
│   ├── os-setup.yaml            Pin RHEL compose
│   ├── setup.yaml               Provision bench + client
│   ├── setup_EL9.yaml           Provision with RHEL 9.0 workaround
│   ├── test.yaml                Core benchmark run
│   ├── site.yml                 Full pipeline (test + master assembly)
│   ├── cleanup.yaml             Reset between runs
│   ├── scalelab-cleanup.yaml    Release ScaleLab hosts
│   ├── run-matrix.yaml          Automated VU sweep
│   └── tasks/                   Reusable task includes
│
├── scripts/
│   ├── release-watcher.py       Detect new GA composes, write bench overlay
│   └── pcp_metrics_log_to_json.py  PCP log → JSON
│
├── inventory/
│   └── generated/
│       └── rhel-ga.ini          Auto-generated bench overlay (gitignored)
│
├── state/
│   └── last-tested.json         Tracks tested composes (gitignored)
│
├── templates/                   Jinja2 templates (Tcl scripts, repo files)
├── group_vars/                  Per-group Ansible variables
├── docs/                        Documentation
└── results/                     Benchmark output (JSON, logs, archives)
```

---

## Requirements

- **Ansible** 2.14+ with `community.postgresql` and `community.general` collections
- **Python 3** on the controller (for PCP log processing and facts merge)
- **jq** on the controller (for master JSON assembly in `site.yml`)
- Two bare-metal hosts (or VMs) running RHEL, reachable via SSH
