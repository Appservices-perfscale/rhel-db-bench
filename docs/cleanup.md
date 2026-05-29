# cleanup.yaml — Reset Between Benchmark Runs

Resets the benchmark state on both **bench** (PostgreSQL) and **client**
(HammerDB load generator) hosts so that the next `test.yaml` invocation starts
from a clean, cold baseline. This playbook does **not** uninstall packages,
undo kernel tuning, or touch any provisioning work done by `setup.yaml` — it
only removes test data and caches.

## Quick start

```bash
ansible-playbook playbooks/cleanup.yaml
```

No extra variables are needed. Run this between successive `test.yaml` or
`site.yml` invocations.

---

## Why clean up between runs?

Without a reset, the next benchmark inherits warm caches, leftover WAL, stale
PCP processes, and a pre-populated database. These carry-over effects make
results non-reproducible:

```
                    Without cleanup                     With cleanup
                ┌─────────────────────┐            ┌─────────────────────┐
  Run N         │  DB loaded, caches  │            │  DB loaded, caches  │
  finishes      │  warm, PCP running  │            │  warm, PCP running  │
                └─────────┬───────────┘            └─────────┬───────────┘
                          │                                  │
                          ▼                                  ▼
                ┌─────────────────────┐            ┌─────────────────────┐
  Between       │  (nothing happens)  │            │  cleanup.yaml runs  │
  runs          │                     │            │  ── see steps below  │
                └─────────┬───────────┘            └─────────┬───────────┘
                          │                                  │
                          ▼                                  ▼
                ┌─────────────────────┐            ┌─────────────────────┐
  Run N+1       │  ✗ Warm buffer pool │            │  ✓ Cold start       │
  starts        │  ✗ Old WAL position │            │  ✓ Fresh WAL        │
                │  ✗ Stale stats      │            │  ✓ Zero stats       │
                │  ✗ Orphan PCP procs │            │  ✓ No orphans       │
                └─────────────────────┘            └─────────────────────┘

  Result:       NOPM inflated or                   NOPM comparable
                inconsistent                       across runs
```

The cleanup ensures every benchmark iteration starts from **identical initial
conditions**, so the only variable between runs is the thing you are actually
testing (RHEL version, kernel, tuning knobs, etc.).

---

## What the playbook does

The playbook contains two plays — one targeting `bench`, one targeting `client`.
They run sequentially (bench first, then client).

### Play 1 — Clean benchmark state on bench (database host)

```
  ┌──────────────────────────────────────────────────────────────────┐
  │                        bench host                                │
  │                                                                  │
  │  1. Kill lingering PCP sampler ─────────────────────────┐        │
  │                                                         │        │
  │  2. Check if PostgreSQL is running ◄────────────────────┘        │
  │          │                                                       │
  │          ▼ (only if active)                                      │
  │  3. pg_terminate_backend() ─── kill benchmark DB connections     │
  │          │                                                       │
  │          ▼                                                       │
  │  4. DROP DATABASE benchdb                                        │
  │          │                                                       │
  │          ▼                                                       │
  │  5. CREATE DATABASE benchdb ─── empty, owned by benchuser        │
  │          │                                                       │
  │          ▼                                                       │
  │  6. GRANT ALL PRIVILEGES + GRANT CREATE ON SCHEMA public         │
  │          │                                                       │
  │          ▼                                                       │
  │  7. pg_stat_reset() ─── zero cumulative statistics               │
  │          │                                                       │
  │          ▼                                                       │
  │  8. CHECKPOINT ─── flush WAL to reset position                   │
  │          │                                                       │
  │          ▼                                                       │
  │  9. Restart PostgreSQL ─── release shared_buffers (270 GB)       │
  │          │                                                       │
  │          ▼                                                       │
  │  10. Wait for port to accept connections                         │
  │          │                                                       │
  │          ▼                                                       │
  │  11. echo 3 > /proc/sys/vm/drop_caches ─── flush OS page cache  │
  │          │                                                       │
  │          ▼                                                       │
  │  12. rm -f *-metrics-samples.log ─── remove old PCP logs         │
  │                                                                  │
  └──────────────────────────────────────────────────────────────────┘
```

#### Step-by-step details

| # | Task | Purpose |
|---|------|---------|
| 1 | **Kill PCP sampler** | If a previous run crashed mid-flight, `pmdumptext` may still be writing metrics. The playbook reads its PID from `<workdir>/.bench-metrics-pmdumptext.pid` and sends `kill`. |
| 2 | **Check PostgreSQL status** | All subsequent database operations are skipped if the service is not running. This makes the playbook safe to call even when PostgreSQL was never started. |
| 3 | **Terminate active connections** | Calls `pg_terminate_backend()` for every session connected to `benchdb`. Required because PostgreSQL refuses to drop a database with active connections. |
| 4 | **Drop benchmark database** | Removes all TPC-C tables, indexes, and data from the previous run. Uses `force: true` as a safety net for any connections that slipped through. |
| 5 | **Recreate empty database** | Creates a fresh `benchdb` owned by `benchuser`, ready for the next `test.yaml` schema build. |
| 6 | **Grant privileges** | Re-applies `ALL PRIVILEGES` and `CREATE ON SCHEMA public` (the latter is needed on PostgreSQL 15+ which restricted the default `public` schema). |
| 7 | **Reset statistics** | `pg_stat_reset()` zeroes all cumulative counters (`pg_stat_user_tables`, `pg_stat_bgwriter`, etc.). Without this, the new run's stats would include carry-over from the old run. |
| 8 | **CHECKPOINT** | Flushes all dirty pages and WAL to disk. This resets the WAL position so the next run's 500 GB `max_wal_size` budget is fully available. |
| 9 | **Restart PostgreSQL** | A restart is the only way to release the 270 GB `shared_buffers` allocation back to the OS. After restart, the buffer pool is empty (cold). |
| 10 | **Wait for port** | Blocks up to 60 seconds until PostgreSQL accepts TCP connections on the configured port. Ensures the service is fully up before the playbook exits. |
| 11 | **Flush OS page cache** | `sync` + `echo 3 > /proc/sys/vm/drop_caches` drops the kernel's page cache, dentry cache, and inode cache. Even after PostgreSQL restarts, the OS may still have data pages cached; this ensures a truly cold start. |
| 12 | **Remove old PCP logs** | Deletes `*-metrics-samples.log` from the work directory so only the next run's metrics remain. |

### Play 2 — Clean benchmark state on client (load generator)

```
  ┌──────────────────────────────────────────────────┐
  │                  client host                      │
  │                                                   │
  │  1. Kill lingering PCP client sampler             │
  │          │                                        │
  │          ▼                                        │
  │  2. echo 3 > /proc/sys/vm/drop_caches             │
  │          │                                        │
  │          ▼                                        │
  │  3. rm -f *-metrics-samples.log                   │
  │                                                   │
  └──────────────────────────────────────────────────┘
```

The client cleanup is lighter — there is no database to drop. The main
concern is orphan PCP processes and stale OS caches from the load
generator's network I/O.

---

## What cleanup does NOT do

| Concern | Handled by |
|---------|-----------|
| Uninstall PostgreSQL or HammerDB | Manual or re-provision |
| Undo kernel tuning (sysctl, THP) | Revert manually or re-run `os-setup.yaml` |
| Remove Podman container images | `podman rmi` manually |
| Release ScaleLab hosts | `playbooks/scalelab-cleanup.yaml` |
| Re-pin the RHEL compose | `playbooks/os-setup.yaml` |

---

## When to run it

```
                    Typical session
  ┌─────────┐     ┌──────────┐     ┌─────────────┐     ┌──────────┐
  │  setup   │────▶│  test    │────▶│  cleanup    │────▶│  test    │──▶ ...
  │  .yaml   │     │  .yaml   │     │  .yaml      │     │  .yaml   │
  └─────────┘     └──────────┘     └─────────────┘     └──────────┘
   (once)          (run 1)          (between runs)       (run 2)
```

- **Before the first run**: not needed (setup.yaml creates a fresh database).
- **Between runs**: always run cleanup.yaml.
- **After the last run**: optional — only needed if you want to free the 270 GB
  shared_buffers allocation immediately.

If you use `run-matrix.yaml`, it calls `site.yml` in a loop but does **not**
call cleanup between iterations automatically — add a cleanup step if you need
cold-start measurements for every VU configuration.

---

## Variables used

All variables come from `inventory.ini` (via `[bench:vars]` and
`[client:vars]`). No extra vars are required at the command line.

| Variable | Source | Used for |
|----------|--------|----------|
| `pgsql_admin_connect_host` | `[bench:vars]` | PostgreSQL connection address (default `127.0.0.1`) |
| `postgresql_service_name` | `[bench:vars]` | Systemd unit name (e.g. `postgresql-17`) |
| `pgsql_superuser` | `[bench:vars]` | Admin login for DROP/CREATE/GRANT |
| `pgsql_superuser_password` | `[bench:vars]` | Admin password |
| `pgsql_port` | `[bench:vars]` | PostgreSQL listen port |
| `pgsql_benchmark_db` | `[bench:vars]` | Database to drop and recreate |
| `pgsql_benchmark_user` | `[bench:vars]` | Owner of the recreated database |
| `hammerdb_remote_workdir` | `[bench:vars]` / `[client:vars]` | Directory holding PCP logs and pidfiles |
