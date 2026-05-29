# scalelab-cleanup.yaml — Release ScaleLab Hosts

Terminates the QUADS self-scheduled assignment created by
[`auto-schedule.yaml`](auto-schedule.md), releasing the bench and client
bare-metal hosts back to the ScaleLab pool. This is the teardown counterpart
to scheduling.

## Quick start

```bash
# Release the assignment recorded in scalelab_assignment.yml:
ansible-playbook playbooks/scalelab-cleanup.yaml

# Terminate a specific assignment by ID:
ansible-playbook playbooks/scalelab-cleanup.yaml -e assignment_id=771

# Nuclear option — terminate ALL your active self-schedule assignments:
ansible-playbook playbooks/scalelab-cleanup.yaml -e terminate_all_active=true
```

---

## Why release hosts?

ScaleLab has a finite pool of bare-metal machines shared across teams. Keeping
hosts scheduled after benchmarking is done blocks other engineers from using
them. The self-scheduling system does not auto-expire assignments — you must
explicitly terminate them.

```
  ┌─────────────────────────────────────────────────────────────────┐
  │                    ScaleLab host pool                           │
  │                                                                │
  │  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐   │
  │  │ h01 │ │ h02 │ │ h03 │ │ h04 │ │ h05 │ │ h06 │ │ h07 │   │
  │  │free │ │free │ │ YOU │ │ YOU │ │free │ │free │ │free │   │
  │  └─────┘ └─────┘ └──┬──┘ └──┬──┘ └─────┘ └─────┘ └─────┘   │
  │                      │       │                                 │
  │                      │  scalelab-cleanup.yaml                  │
  │                      ▼       ▼                                 │
  │                                                                │
  │  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐   │
  │  │ h01 │ │ h02 │ │ h03 │ │ h04 │ │ h05 │ │ h06 │ │ h07 │   │
  │  │free │ │free │ │free │ │free │ │free │ │free │ │free │   │
  │  └─────┘ └─────┘ └─────┘ └─────┘ └─────┘ └─────┘ └─────┘   │
  │                                                                │
  │  All hosts returned to pool — available for other teams        │
  └─────────────────────────────────────────────────────────────────┘
```

---

## How it works

```
  Controller                              QUADS API
  ──────────                              ────────
       │
       │  1. Read scalelab_assignment.yml
       │     (or use -e assignment_id=...)
       │
       │  2. Remove stale SSH host keys
       │     ssh-keygen -R <bench>, ssh-keygen -R <client>
       │
       │  3. POST /login/
       │─────────────────────────────────▶  Authenticate
       │◀─────────────────────────────────  { auth_token }
       │
       │  4. GET /assignments/<id>
       │─────────────────────────────────▶  Show status (validated?)
       │◀─────────────────────────────────  { validated: true, ... }
       │
       │  5. POST /assignments/terminate/<id>
       │─────────────────────────────────▶  Release all hosts
       │◀─────────────────────────────────  HTTP 200
       │
       │  6. rm scalelab_assignment.yml
       │
       ▼
     DONE — hosts released
```

---

## Three ways to identify what to terminate

The playbook resolves which assignment(s) to terminate using a priority
cascade:

```
  Priority 1 (highest)         Priority 2                 Priority 3
  ─────────────────────        ──────────                 ──────────
  -e assignment_id=771         scalelab_assignment.yml    -e terminate_all_active=true
                               (written by                (queries API for ALL your
  Terminates exactly           auto-schedule.yaml)        active self-scheduled
  this one assignment.                                    assignments)
                               Reads the file and
                               extracts assignment_id.

  ┌─────────────────┐          ┌─────────────────┐       ┌─────────────────┐
  │ Single ID       │          │ Record file      │       │ API query       │
  │ from CLI        │          │ from disk        │       │ for all active  │
  └────────┬────────┘          └────────┬────────┘       └────────┬────────┘
           │                            │                          │
           └────────────────────────────┼──────────────────────────┘
                                        │
                                        ▼
                               ┌─────────────────┐
                               │ Terminate each   │
                               │ assignment ID    │
                               └─────────────────┘
```

If none of the three sources yields an assignment ID, the playbook prints a
message and exits cleanly (no error).

---

## Step-by-step task breakdown

| # | Task | Details |
|---|------|---------|
| 1 | **Load QUADS config** | Reads `quads_cfg.yaml` for API server, username, and password. |
| 2 | **Check for assignment record** | Stats `scalelab_assignment.yml` to see if it exists. |
| 3 | **Load assignment record** | If the file exists and no explicit `assignment_id` or `terminate_all_active` was passed, loads the YAML to get `assignment_id` and `scheduled_hosts`. |
| 4 | **Remove stale SSH host keys** | Runs `ssh-keygen -R` for each host in the assignment. Prevents "host key changed" errors if the hosts get reassigned and reprovisioned. |
| 5 | **Log in to QUADS API** | Authenticates and obtains a bearer token. |
| 6 | **Build termination list** | Resolves which assignment ID(s) to terminate (see priority cascade above). |
| 7 | **Show pre-terminate status** | GETs each assignment to display its `validated` state before termination. |
| 8 | **Terminate assignment(s)** | POSTs to `/assignments/terminate/<id>` for each assignment. |
| 9 | **Remove assignment record** | Deletes `scalelab_assignment.yml` so the next `auto-schedule.yaml` run starts fresh. |

---

## Lifecycle with auto-schedule

```
  ┌───────────────────────────────────────────────────────────────────┐
  │                        Full lifecycle                             │
  │                                                                   │
  │  ┌──────────────┐                         ┌──────────────────┐   │
  │  │auto-schedule │                         │scalelab-cleanup  │   │
  │  │    .yaml     │                         │    .yaml         │   │
  │  │              │                         │                  │   │
  │  │ Creates:     │     Benchmarking        │ Consumes:        │   │
  │  │ • assignment │────▶ os-setup ─────────▶│ • assignment     │   │
  │  │ • inventory  │     setup              │   record         │   │
  │  │ • SSH keys   │     test/site          │ • SSH keys       │   │
  │  │              │     cleanup            │                  │   │
  │  │              │     (repeat as needed)  │ Terminates:      │   │
  │  │              │                         │ • QUADS          │   │
  │  │              │                         │   assignment     │   │
  │  └──────────────┘                         └──────────────────┘   │
  │       ▲                                          │               │
  │       │              Hosts released              │               │
  │       └──────────── back to pool ◀───────────────┘               │
  │                                                                   │
  └───────────────────────────────────────────────────────────────────┘
```

---

## Extra variables

| Variable | Default | Description |
|----------|---------|-------------|
| `assignment_id` | *(from record file)* | Terminate a specific assignment (overrides the record file) |
| `terminate_all_active` | `false` | When `true`, queries and terminates **all** your active self-schedule assignments |

---

## Configuration

Uses the same `quads_cfg.yaml` as `auto-schedule.yaml`. See
[auto-schedule.md](auto-schedule.md#configuration-quads_cfgyaml) for the full
variable reference.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `"No assignment to terminate"` | No record file, no `-e assignment_id`, no active assignments | Nothing to do — hosts were already released or never scheduled |
| QUADS login failure | Wrong credentials in `quads_cfg.yaml` | Verify `quads_username`, `quads_user_domain`, `quads_password` |
| Assignment shows `validated: false` | QUADS hasn't validated the assignment yet (normal for recent schedules) | Termination still works regardless of validation state |
| `scalelab_assignment.yml` still exists after error | Terminate API call failed | Check QUADS server status; retry, or use `-e assignment_id=<id>` to target explicitly |
