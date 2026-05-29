# DB-CPT Test Logic

## What Is This?

A **PostgreSQL TPC-C benchmark** that compares RHEL versions for database performance.
Two bare-metal hosts on the **same rack** (same top-of-rack switch, so network is not the bottleneck):

| Host | Role |
|------|------|
| **bench-vm** | Runs PostgreSQL (the system under test). |
| **client-vm** | Runs HammerDB inside a Podman container, generates TPC-C load against bench-vm over the network. |

The goal is to measure **NOPM** (New Orders Per Minute) and **TPM** (Transactions Per Minute) and compare them across RHEL releases.

---

## Playbook Order

```
1. auto-schedule.yaml   — (optional) Reserve two same-rack ScaleLab hosts via QUADS API.
2. os-setup.yaml        — Pin the exact RHEL compose on bench and client (distro-sync).
3. setup.yaml           — One-time: install PostgreSQL, HammerDB, PCP, kernel tuning.
4. test.yaml            — The actual benchmark run (this is what most of this doc explains).
5. site.yml             — Full pipeline: test.yaml + master JSON assembly with OS facts.
6. cleanup.yaml         — Reset state between runs so the next test.yaml starts clean.
7. scalelab-cleanup.yaml — Release ScaleLab hosts when finished.
```

`test.yaml` is the core. You run it once per benchmark measurement.
`site.yml` wraps `test.yaml` and adds artifact assembly.

**Detailed documentation for each playbook:**

| Playbook | Doc |
|----------|-----|
| `auto-schedule.yaml` | [auto-schedule.md](auto-schedule.md) |
| `setup.yaml` | [setup.md](setup.md) |
| `setup_EL9.yaml` | [setup_EL9.md](setup_EL9.md) |
| `test.yaml` | This document |
| `site.yml` | [site.md](site.md) |
| `cleanup.yaml` | [cleanup.md](cleanup.md) |
| `scalelab-cleanup.yaml` | [scalelab-cleanup.md](scalelab-cleanup.md) |

---

## test.yaml Flow (Step by Step)

### Play 1 — Prep the database host (bench)

1. **Ensure PostgreSQL is running** from setup.yaml.
2. **Apply low-variance tuning** via `ALTER SYSTEM` + restart (see "Why These Settings" below).
3. **Record test start time** (epoch seconds, for the JSON report).
4. **CHECKPOINT + VACUUM** — flush any dirty data and reclaim dead rows *before* the timed run, so PostgreSQL is clean.
5. **Start PCP metrics sampler** — background `pmdumptext` records CPU, memory, disk, and network every 4 seconds on the bench host.

### Play 2 — Build the TPC-C schema (client)

1. **Verify HammerDB container image** exists (pulled by setup.yaml).
2. **Deploy Tcl scripts** (`setup.tcl`, `run.tcl`, `cleanup.tcl`) from Jinja2 templates with current inventory values baked in.
3. **TCP connectivity check** — wait for bench:5432 to be reachable.
4. **Start PCP sampler on the client** too (CPU/mem/disk of the load generator).
5. **Run `setup.tcl`** inside HammerDB container — this creates the TPC-C warehouse tables and populates them (2000 warehouses by default). Takes a while.
6. **Assert FINISHED SUCCESS** — fail fast if schema build did not complete.

### Play 3 — Prewarm the database (bench)

1. **`pg_prewarm`** loads every user table and materialized view into PostgreSQL `shared_buffers` (RAM).

**Why prewarm?** Without it the first minutes of the benchmark are slower because PostgreSQL fetches pages from disk into the buffer pool on demand. With prewarm, the entire dataset is already in RAM before the timer starts, so every run begins from the same warm state and results are consistent from second one:

```
With prewarm:             Without prewarm:

TPM |  ████████████       TPM |      ▄▄████████
    |  ████████████           |    ▄█████████████
    |  ████████████           |  ▄███████████████
    └──────────► time         └──────────────► time
```

### Play 4 — Timed HammerDB run (client)

1. **(Optional) Drop OS page cache** — disabled by default because we *want* the prewarm to persist.
2. **Run `run.tcl`** — HammerDB drives 56 virtual users against the TPC-C database for 5 minutes (2 min ramp-up + 5 min timed).
3. **Stop PCP sampler** on client and fetch the log back to the controller.
4. **Assert FINISHED SUCCESS**.
5. **Parse results** — regex-extract NOPM and TPM from HammerDB stdout.
6. **Record test end time**.

### Play 5 — Stop metrics and drop the database (bench)

1. **Stop PCP sampler** on bench and fetch the log.
2. **Drop the benchmark database** — the schema is single-use; cleanup.yaml or the next test.yaml will recreate it.

### Play 6 — Write JSON report (localhost)

1. Collect all facts from bench and client: OS version, kernel, tuning applied, NOPM, TPM, timing, PCP filenames.
2. **Merge PCP metric logs** into the JSON via `scripts/pcp_metrics_log_to_json.py`.
3. Output: `results/<run-id>-benchmark-report.json`.

---

## Why These PostgreSQL Settings

Every tuning knob exists to **eliminate variance** so that the only variable between runs is the RHEL version itself. This is a benchmark, not production — some settings are intentionally unsafe.

### Memory — Make the Entire Database Fit in RAM

| Setting | Value | Why |
|---------|-------|-----|
| `shared_buffers` | **270 GB** | 2000 TPC-C warehouses produce ~200 GB of data. 270 GB ensures the **whole database fits in PostgreSQL's buffer pool** with room to spare. If the DB does not fit in RAM, some queries hit disk, and disk latency introduces noise that has nothing to do with the RHEL version we are testing. |
| `effective_cache_size` | **310 GB** | Tells the query planner "this much total cache exists (shared_buffers + OS page cache)." A higher value makes the planner prefer index scans over sequential scans, which is the right choice when data is in memory. Does not allocate any memory. |
| `work_mem` | **128 MB** | Per-sort/hash budget. Prevents sorts from spilling to disk temp files, which would add I/O variance. |
| `maintenance_work_mem` | **1 GB** | Speeds up VACUUM and CREATE INDEX during the schema build phase (not the timed run). |

If `shared_buffers` is left empty in inventory, the playbook **auto-sizes** it: `(total_RAM - 8 GB OS reserve) * 75%`.

### WAL — Prevent Checkpoints During the Timed Run

A PostgreSQL **checkpoint** flushes all dirty pages to disk. During a checkpoint the server does heavy I/O, which tanks throughput for several seconds. We must avoid checkpoints during the 5-minute timed window.

| Setting | Value | Why |
|---------|-------|-----|
| `max_wal_size` | **500 GB** | PostgreSQL triggers a checkpoint when WAL accumulated since the last checkpoint exceeds `max_wal_size`. A 5-minute TPC-C run at high throughput generates perhaps 50-100 GB of WAL. 500 GB is deliberately oversized so that **WAL volume alone never forces a checkpoint**. |
| `checkpoint_timeout` | **86400 s** (24 hours) | The other checkpoint trigger is time. Default is 5 minutes — exactly our test window, which would guarantee a checkpoint mid-run. 24 hours means the timer never fires during the test. |
| `checkpoint_completion_target` | **0.9** | If a checkpoint somehow does occur, spread I/O over 90% of the interval instead of a burst. Minor safety net. |
| `min_wal_size` | **4 GB** | Floor for WAL recycling after a checkpoint. Secondary to `max_wal_size`. |

We also run a **manual CHECKPOINT** right *before* the test starts (Play 1, step 4) so the WAL counter resets and the 500 GB budget is fresh.

### Durability — Turned Off (Benchmark Only)

| Setting | Value | Why |
|---------|-------|-----|
| `fsync` | **off** | Skips `fsync()` calls. Data is not crash-safe, but we eliminate sync I/O jitter entirely. This is a benchmark, not production. |
| `synchronous_commit` | **off** | Transactions do not wait for WAL to be flushed to disk before returning. Reduces commit latency and removes I/O variance. Pairs with `fsync=off`. |

### Background Processes — Silenced

| Setting | Value | Why |
|---------|-------|-----|
| `autovacuum` | **off** | Prevents background vacuum workers from doing random I/O during the timed run. We run a manual `VACUUM` in Play 1 before the benchmark starts, so dead-row cleanup is already done. |
| `bgwriter_lru_maxpages` | **0** | Disables the background writer (no background dirty-page flushes = no surprise I/O). |
| `bgwriter_flush_after` | **0** | Disables batch flush-after behavior in the background writer. |

### Planner Hints — Tuned for NVMe

| Setting | Value | Why |
|---------|-------|-----|
| `random_page_cost` | **1.1** | Default is 4.0 (spinning disks). On NVMe, random reads are almost as fast as sequential. This tells the planner to prefer index scans, which is optimal for TPC-C. |
| `cpu_tuple_cost` | **0.03** | Slightly higher than default (0.01), nudges the planner toward index access when the storage is fast and CPU is the limiter. |

### Logging — Minimal but Useful

| Setting | Value | Why |
|---------|-------|-----|
| `log_checkpoints` | **on** | So we can verify in the PostgreSQL log that **no checkpoint occurred** during the timed window. If one did, the run is suspect. |
| `log_min_duration_statement` | **10 ms** | Log any query slower than 10 ms for debugging stragglers, without the overhead of full statement logging. |
| `lc_messages` | **C** | Fixed locale for consistent log output across hosts. |

---

## Kernel / OS Tuning (setup.yaml)

| Setting | Why |
|---------|-----|
| `vm.swappiness = 1` | Almost never swap. PostgreSQL manages its own memory via `shared_buffers`; swapping pages in and out adds huge latency spikes. |
| `kernel.numa_balancing = 0` | Disables automatic NUMA page migration. Prevents the kernel from moving PostgreSQL's buffer pool pages between NUMA nodes, which causes unpredictable stalls. |
| **Transparent Huge Pages = never** | THP compaction can freeze all allocations for milliseconds. Disabling THP removes this source of latency jitter. A systemd unit (`disable-thp.service`) runs before PostgreSQL starts. |

---

## PCP Metrics — What We Capture

[PCP](https://pcp.io/) (Performance Co-Pilot) `pmdumptext` samples metrics every 4 seconds on both bench and client during the entire benchmark window:

- **CPU**: `kernel.all.cpu.user`, `kernel.all.cpu.sys`, `kernel.all.cpu.idle`
- **Memory**: `mem.util.used`, `mem.util.free`
- **Disk (aggregate)**: `disk.all.read`, `disk.all.write`
- **Disk (per-device)**: `disk.dev.read[nvme0n1]`, `disk.dev.write[nvme0n1]`, etc.
- **Network (per-interface)**: `network.interface.in.bytes[eth0]`, `network.interface.out.bytes[eth0]`

These end up in the JSON report so you can correlate NOPM/TPM with actual resource usage.

---

## cleanup.yaml — Between Runs

Resets the benchmark state without uninstalling anything:

1. Stop any lingering PCP samplers.
2. Terminate connections, drop, and recreate the benchmark database (empty).
3. Reset PostgreSQL cumulative stats (`pg_stat_reset()`).
4. CHECKPOINT + restart PostgreSQL (releases shared_buffers).
5. Flush OS page cache (`echo 3 > /proc/sys/vm/drop_caches`).
6. Remove old PCP log files.

Run this between test.yaml invocations so each benchmark starts from a clean, cold state.

---

## Quick Reference

```bash
# (Optional) Reserve ScaleLab hosts:
ansible-playbook playbooks/auto-schedule.yaml -e "workload_name='DB-CPT RHEL 10'"

# First time only (installs everything):
ansible-playbook playbooks/setup.yaml

# Run a benchmark (test only):
ansible-playbook playbooks/test.yaml

# Run full pipeline (test + master JSON):
ansible-playbook playbooks/site.yml

# Override virtual users:
ansible-playbook playbooks/site.yml -e hammerdb_virtual_users=112

# Clean up between runs:
ansible-playbook playbooks/cleanup.yaml

# Release ScaleLab hosts when done:
ansible-playbook playbooks/scalelab-cleanup.yaml
```

Results land in `results/<timestamp>-benchmark-report.json`.
Master JSON (from `site.yml`) lands in `results/<timestamp>-master.json`.
