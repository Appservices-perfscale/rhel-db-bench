# auto-schedule.yaml — Reserve ScaleLab Hosts via QUADS

Grabs two bare-metal hosts from
[ScaleLab](https://wiki.rdu2.scalelab.redhat.com/) via the QUADS v3
self-scheduling API. First host = **bench** (PostgreSQL), second = **client**
(HammerDB). They must land on the same rack so network noise stays out of
NOPM.

## Quick start

```bash
# Minimal — reserves 2 hosts with the description you provide:
ansible-playbook playbooks/auto-schedule.yaml -e "workload_name='DB-CPT RHEL 10 baseline'"

# Pin exact hostnames (must be on the same rack):
ansible-playbook playbooks/auto-schedule.yaml \
  -e "workload_name='DB-CPT RHEL 10'" \
  -e "schedule_servers=f24-h07-000-r650.rdu2.scalelab.redhat.com,f24-h08-000-r650.rdu2.scalelab.redhat.com"

# Override host count (default 2):
ansible-playbook playbooks/auto-schedule.yaml -e "workload_name='DB-CPT'" -e num_hosts=2
```

When finished with the hosts, release them with
[`scalelab-cleanup.yaml`](scalelab-cleanup.md).

---

## How it works — end to end

```
  YOU (controller)                    QUADS API (quads2.rdu2.scalelab.redhat.com)
  ──────────────                      ──────────────────────────────────────────
       │
       │  1. POST /register
       │─────────────────────────────────────▶  Register user (idempotent)
       │
       │  2. POST /login/
       │─────────────────────────────────────▶  Get auth token
       │◀─────────────────────────────────────  { auth_token: "..." }
       │
       │  3. GET /available?can_self_schedule=true
       │─────────────────────────────────────▶  List available hosts
       │◀─────────────────────────────────────  [ "f24-h07-...", "f24-h08-...", ... ]
       │
       │  ┌──────────────────────────────────────────────────────┐
       │  │  LOCAL: Filter hosts by preferred_models, then       │
       │  │  group by rack prefix and pick the first rack with   │
       │  │  ≥ num_hosts hosts available.                        │
       │  │                                                      │
       │  │  Same-rack constraint: bench + client must share     │
       │  │  a rack (e.g. f24-h07 + f24-h08, NOT f24 + f27)     │
       │  │  to stay on the same top-of-rack switch.             │
       │  └──────────────────────────────────────────────────────┘
       │
       │  4. POST /assignments/self
       │─────────────────────────────────────▶  Create assignment
       │◀─────────────────────────────────────  { id: 771, cloud: "cloud02" }
       │
       │  5. POST /schedules (per host)
       │─────────────────────────────────────▶  Attach host to assignment
       │
       │  ┌──────────────────────────────────────────────────────┐
       │  │  LOCAL: Write output files                           │
       │  │    • scalelab_assignment.yml  (for cleanup later)    │
       │  │    • inventory.local.ini      (bench + client IPs)   │
       │  └──────────────────────────────────────────────────────┘
       │
       │  6. ssh-keygen -R <host>  (clear stale SSH keys)
       │
       ▼
     DONE — hosts are scheduled, inventory is ready
```

---

## The same-rack constraint

DB-CPT requires that bench and client are on the **same rack** so they share a
single top-of-rack (ToR) switch. This guarantees sub-millisecond network
latency and removes network variability from benchmark results:

```
        Rack f24 (same ToR switch)              Rack f27 (different switch)
       ┌──────────────────────┐                ┌──────────────────────┐
       │  ┌──────┐ ┌──────┐  │                │  ┌──────┐            │
       │  │bench │ │client│  │                │  │client│            │
       │  │ h07  │ │ h08  │  │                │  │ h03  │            │
       │  └──┬───┘ └──┬───┘  │                │  └──┬───┘            │
       │     │        │      │                │     │                │
       │  ═══╪════════╪═══   │                │  ═══╪═══             │
       │     ToR switch      │                │     ToR switch       │
       └──────────────────────┘                └──────────────────────┘
             │        │                              │
             └────────┘  ◀── ~0.05 ms                │
                                                     │
        ═══════════════════════════════════════════════
                     spine / fabric  ◀── +0.2-1.0 ms added

  ✓ Same rack  = consistent, low latency
  ✗ Cross-rack = variable latency from spine hops, defeats the benchmark
```

The playbook enforces this by parsing rack prefixes from hostnames (e.g.
`f24-h07-000-r650...` → rack `f24`) and only selecting hosts that share one.

---

## Host selection modes

The playbook supports three ways to choose which hosts get scheduled:

```
                          ┌────────────────────────┐
                          │  schedule_servers set?  │
                          └─────────┬──────────────┘
                                    │
                      ┌─────────────┴─────────────┐
                      │ YES                        │ NO
                      ▼                            ▼
              ┌───────────────┐         ┌──────────────────────┐
              │ Pinned mode   │         │ preferred_models set │
              │ Use exact     │         │ in quads_cfg.yaml?   │
              │ hostnames     │         └──────────┬───────────┘
              └───────────────┘                    │
                                     ┌─────────────┴───────────┐
                                     │ YES                      │ NO
                                     ▼                          ▼
                             ┌───────────────┐         ┌───────────────┐
                             │ Model filter  │         │ Any available │
                             │ Query per     │         │ All self-     │
                             │ model, merge  │         │ schedulable   │
                             │ pools         │         │ hosts         │
                             └───────────────┘         └───────────────┘
```

### 1. Pinned hostnames (`schedule_servers`)

Pass exact hostnames as a comma-separated list. The playbook validates they
share one rack and schedules them directly.

```bash
-e "schedule_servers=f24-h07-000-r650.rdu2.scalelab.redhat.com,f24-h08-000-r650.rdu2.scalelab.redhat.com"
```

### 2. Model preference (`preferred_models` in `quads_cfg.yaml`)

```yaml
preferred_models:
  - r650
  - r660
```

The playbook queries available hosts for each model (in order), merges the
pools, then picks the first rack with enough hosts. Models earlier in the list
get priority.

### 3. Any available (default)

When neither `schedule_servers` nor `preferred_models` is set, the playbook
uses all self-schedulable hosts and picks the first rack with `num_hosts`
available.

---

## Output files

### `scalelab_assignment.yml`

Written to the project root. Contains the assignment ID, cloud name, selected
hosts, and rack — everything `scalelab-cleanup.yaml` needs to release the
hosts later.

```yaml
assignment_id: '771'
assignment_description: DB-CPT for RHEL
cloud_name: cloud02
host_selection_mode: preferred_models
preferred_models:
  - r640
selected_rack: f35
scheduled_hosts:
  - f35-h17-000-r640.rdu2.scalelab.redhat.com
  - f35-h18-000-r640.rdu2.scalelab.redhat.com
bench_host: f35-h17-000-r640.rdu2.scalelab.redhat.com
client_host: f35-h18-000-r640.rdu2.scalelab.redhat.com
```

### `inventory.local.ini`

Written from the `inventory.local.ini.j2` template. Maps the first scheduled
host to `bench-vm` and the second to `client-vm`, with the SSH password
injected from `quads_cfg.yaml` or a pre-existing `inventory.local.ini`.

```ini
[bench]
bench-vm ansible_host=f35-h17-000-r640.rdu2.scalelab.redhat.com ansible_ssh_pass=...

[client]
client-vm ansible_host=f35-h18-000-r640.rdu2.scalelab.redhat.com ansible_ssh_pass=...
```

This file is loaded as a secondary inventory (`-i inventory.local.ini`) by all
downstream playbooks.

---

## Configuration: `quads_cfg.yaml`

All QUADS API settings live in `quads_cfg.yaml` at the project root:

| Key | Required | Description |
|-----|----------|-------------|
| `quads_api_server` | Yes | QUADS API hostname (e.g. `quads2.rdu2.scalelab.redhat.com`) |
| `quads_username` | Yes | Your QUADS username (Kerberos principal, without domain) |
| `quads_user_domain` | Yes | Domain suffix for email (e.g. `redhat.com`) |
| `quads_password` | Yes | QUADS API password |
| `scalelab_ssh_pass` | Yes | SSH password for scheduled hosts (written into `inventory.local.ini`) |
| `preferred_models` | No | List of server models to prefer (e.g. `[r650, r660]`), or omit for any |
| `preferred_rack` | No | Pin to a specific rack prefix (e.g. `f24`) |
| `preferred_bench_host` | No | Pin the bench role to a specific hostname |
| `preferred_client_host` | No | Pin the client role to a specific hostname |

---

## Extra variables

| Variable | Default | Description |
|----------|---------|-------------|
| `workload_name` | *(required)* | Description for the QUADS assignment (e.g. `"DB-CPT RHEL 10 baseline"`) |
| `num_hosts` | `2` | Number of hosts to reserve (minimum 2: bench + client) |
| `schedule_servers` | *(not set)* | Comma-separated exact hostnames to schedule (bypasses model filtering) |

---

## Lifecycle: schedule → use → release

```
  ┌──────────────────┐      ┌──────────────┐      ┌───────────────────┐
  │ auto-schedule     │      │  Benchmark   │      │ scalelab-cleanup  │
  │     .yaml         │─────▶│  workflow    │─────▶│     .yaml         │
  │                   │      │              │      │                   │
  │ Reserves hosts,   │      │ os-setup →   │      │ Terminates the    │
  │ writes inventory  │      │ setup →      │      │ QUADS assignment, │
  │ + assignment      │      │ test/site →  │      │ releases hosts,   │
  │ record            │      │ cleanup      │      │ removes record    │
  └──────────────────┘      └──────────────┘      └───────────────────┘
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `"no rack has 2 available host(s)"` | Not enough same-rack hosts free for your model | Try a different `preferred_models` list, or wait for hosts to free up |
| `403` on assignment creation | You already have an active self-schedule | Run `scalelab-cleanup.yaml` first, or the playbook will reuse the existing assignment |
| `"set both preferred_bench_host and preferred_client_host"` | Only one of the pair was set | Set both or neither in `quads_cfg.yaml` |
| `"bench and client must share a rack prefix"` | Preferred hosts are on different racks | Choose hosts from the same rack |
| SSH connection failures after scheduling | Stale host keys in `~/.ssh/known_hosts` | The playbook removes them automatically; if it still fails, run `ssh-keygen -R <hostname>` manually |
