# site.yml — Full Benchmark Pipeline

The top-level playbook that runs a complete benchmark cycle: execute the
HammerDB TPC-C workload via `test.yaml`, then fetch all artifacts from the
remote hosts and assemble a master JSON file that combines results, monitoring
data, and OS facts into a single document.

## Quick start

```bash
# Run with default settings (56 virtual users):
ansible-playbook playbooks/site.yml

# Override virtual users:
ansible-playbook playbooks/site.yml -e hammerdb_virtual_users=112

# Custom results directory:
ansible-playbook playbooks/site.yml -e perf_results_path=/data/results
```

Requires `setup.yaml` to have been run once before the first benchmark.

---

## site.yml vs test.yaml — when to use which

```
  ┌───────────────────────────────────────────────────────────────────────┐
  │                           site.yml                                    │
  │                                                                       │
  │  ┌─────────────────────────────────────────────────────────────────┐  │
  │  │                        test.yaml                                │  │
  │  │                                                                 │  │
  │  │  Play 1: Prep bench (tuning, PCP, checkpoint)                   │  │
  │  │  Play 2: Build TPC-C schema on client                           │  │
  │  │  Play 3: Prewarm database on bench                              │  │
  │  │  Play 4: Timed HammerDB run on client                           │  │
  │  │  Play 5: Stop metrics on bench                                  │  │
  │  │  Play 6: Write benchmark-report.json + results.json             │  │
  │  │          + monitoring.json on localhost                          │  │
  │  └─────────────────────────────────────────────────────────────────┘  │
  │                                                                       │
  │  Play 7: Fetch facts.json files, assemble master.json on localhost    │
  │                                                                       │
  └───────────────────────────────────────────────────────────────────────┘
```

| Use case | Playbook |
|----------|----------|
| Quick benchmark, you only need NOPM/TPM and PCP data | `test.yaml` |
| Full pipeline with master JSON including OS facts | `site.yml` |
| Automated matrix runs (multiple VU counts) | `run-matrix.yaml` (calls `site.yml` internally) |

`site.yml` is a superset — it `import_playbook`s `test.yaml` as Phase 1, then
adds an artifact assembly phase on top.

---

## How it works — two phases

### Phase 1: Run the benchmark (`import_playbook: test.yaml`)

This is the entire `test.yaml` flow (6 plays). See [test-logic.md](test-logic.md)
for the full step-by-step breakdown. At the end of Phase 1, the following files
exist in the results directory:

| File | Content |
|------|---------|
| `<run_id>-benchmark-report.json` | Full report with PCP time-series samples embedded |
| `<run_id>-results.json` | HammerDB KPIs only (NOPM, TPM, timing) |
| `<run_id>-monitoring.json` | Aggregate PCP statistics (mean, max, p95 per metric) |
| `<run_id>-bench-metrics-samples.log` | Raw PCP data from the bench host |
| `<run_id>-client-metrics-samples.log` | Raw PCP data from the client host |

### Phase 2: Fetch facts and assemble master JSON

This phase runs as a single play on `localhost`. It collects OS-level facts
from `os-setup.yaml` output and merges everything into one master document.

```
  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
  │  facts.json      │    │  results.json    │    │  monitoring.json │
  │  (per host, from │    │  (NOPM, TPM,     │    │  (mean/max/p95   │
  │   os-setup.yaml) │    │   timing)        │    │   PCP stats)     │
  └────────┬─────────┘    └────────┬─────────┘    └────────┬─────────┘
           │                       │                       │
           └───────────────────────┼───────────────────────┘
                                   │
                                   ▼
                          ┌─────────────────┐
                          │   jq merge      │
                          │                 │
                          │  {              │
                          │    id: "...",   │
                          │    started: "", │
                          │    ended: "",   │
                          │    facts: {},   │
                          │    results: {}, │
                          │    monitoring:{}│
                          │  }              │
                          └────────┬────────┘
                                   │
                                   ▼
                          ┌─────────────────┐
                          │  master.json    │
                          └─────────────────┘
```

#### Step by step

1. **Locate `facts.json` files** — scans `results/os-prep/*/facts.json`
   (written by `os-setup.yaml` after distro-sync and reboot).

2. **Merge facts** — a Python one-liner reads each host's `facts.json` and
   combines them into `{ "bench-vm": {...}, "client-vm": {...} }`.

3. **Generate run metadata** — creates a unique run ID and converts the
   start/end epoch timestamps (from `test.yaml`) to ISO 8601 format.

4. **Assemble with `jq`** — merges facts, results, and monitoring into a
   single master JSON document with this schema:

   ```json
   {
     "id": "run-2026-05-29T08_39_27...",
     "started": "2026-05-29T08:39:27+00:00",
     "ended": "2026-05-29T08:47:12+00:00",
     "facts": {
       "bench-vm": { "ansible_kernel": "...", "ansible_distribution": "..." },
       "client-vm": { "ansible_kernel": "...", "ansible_distribution": "..." }
     },
     "results": {
       "nopm": "438521",
       "tpm": "1012043"
     },
     "monitoring": {
       "bench": { "cpu_user_mean": 42.3, "mem_used_max": 285000000000 },
       "client": { "cpu_user_mean": 18.7 }
     }
   }
   ```

5. **Cleanup** — removes the temporary staging file used during the merge.

6. **Report** — prints the path to the master JSON and lists all generated
   artifacts.

---

## Output artifacts

After a successful `site.yml` run, the `results/` directory contains:

```
results/
├── 20260529-083927-benchmark-report.json    ← Full report (PCP embedded)
├── 20260529-083927-results.json             ← NOPM + TPM only
├── 20260529-083927-monitoring.json          ← Aggregate PCP stats
├── 20260529-083927-bench-metrics-samples.log   ← Raw PCP (bench)
├── 20260529-083927-client-metrics-samples.log  ← Raw PCP (client)
├── 20260529-083927-master.json              ← Combined master document
└── os-prep/
    ├── bench-vm/
    │   └── facts.json                       ← Full Ansible facts (bench)
    └── client-vm/
        └── facts.json                       ← Full Ansible facts (client)
```

### What goes where

| File | Use case |
|------|----------|
| **master.json** | Single file for dashboards, CI comparisons, or archival — has everything |
| **benchmark-report.json** | Detailed analysis — includes per-sample PCP time series |
| **results.json** | Quick check — just the NOPM/TPM numbers and timing |
| **monitoring.json** | Resource usage summary without the full time series |
| **\*-metrics-samples.log** | Raw data for custom analysis or re-processing |

---

## How master.json relates to the other files

```
  master.json
  ┌─────────────────────────────────────────────────┐
  │                                                 │
  │  .id          ← unique run identifier           │
  │  .started     ← ISO 8601 start time             │
  │  .ended       ← ISO 8601 end time               │
  │                                                 │
  │  .facts       ← from os-prep/*/facts.json       │
  │  │             (Ansible gather_facts output      │
  │  │              after distro-sync + reboot)      │
  │  │                                               │
  │  .results     ← from <run_id>-results.json       │
  │  │             (NOPM, TPM, timing)               │
  │  │                                               │
  │  .monitoring  ← from <run_id>-monitoring.json    │
  │               (mean/max/p95 for CPU, mem, disk)  │
  │                                                 │
  └─────────────────────────────────────────────────┘
```

---

## Relation to other playbooks

```
  ┌──────────────┐
  │auto-schedule │  (optional — reserves ScaleLab hosts)
  └──────┬───────┘
         ▼
  ┌──────────────┐
  │  os-setup    │  (pins RHEL compose, writes facts.json)
  └──────┬───────┘
         ▼
  ┌──────────────┐
  │   setup      │  (one-time: PostgreSQL, HammerDB, PCP, kernel tuning)
  └──────┬───────┘
         ▼
  ┌══════════════╗
  ║  site.yml    ║  ◀── YOU ARE HERE
  ║  (test.yaml  ║
  ║   + master   ║
  ║   assembly)  ║
  ╚══════╤═══════╝
         ▼
  ┌──────────────┐
  │  cleanup     │  (reset between runs)
  └──────┬───────┘
         ▼
  ┌──────────────┐
  │ (next run)   │  site.yml again, or run-matrix.yaml for automated sweeps
  └──────────────┘
```

---

## Extra variables

| Variable | Default | Description |
|----------|---------|-------------|
| `hammerdb_virtual_users` | `56` (from inventory) | Number of HammerDB virtual users for the TPC-C run |
| `perf_results_path` | `results/` | Override the output directory for all artifacts |
| `perf_run_stamp` | Auto-generated timestamp | Override the run ID prefix for filenames |

---

## Prerequisites

- `setup.yaml` has been run (PostgreSQL, HammerDB, PCP installed and configured).
- `os-setup.yaml` has been run (for `facts.json` to exist — optional but recommended).
- `jq` is installed on the controller (used for the master JSON assembly).
- `python3` is installed on the controller (used for facts merging).
