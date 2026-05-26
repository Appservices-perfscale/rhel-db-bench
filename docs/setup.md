# setup.yaml — Provision Bench + Client

One-time provisioning playbook for the benchmark topology: a **bench** host
(PostgreSQL SUT) and a **client** host (HammerDB load generator via Podman).

Use this playbook for **latest RHEL releases** (10.x, 9.7+, etc.) where PGDG
packages install cleanly against the system libraries.
For RHEL 9.0, use [`setup_EL9.yaml`](setup_EL9.md) instead — it carries an
extra workaround for library-version mismatches between PGDG and the older
base OS.

## Quick start

```bash
# Full run (bench + client):
ansible-playbook playbooks/setup.yaml

# Bench only (re-run after inventory changes):
ansible-playbook playbooks/setup.yaml -i inventory.ini -i inventory.local.ini --limit bench

# Client only:
ansible-playbook playbooks/setup.yaml --limit client
```

## What the playbook does

### Play 1 — Provision database host (`bench`)

#### 1. System preparation

Installs base packages needed by later steps (`python3`, `xfsprogs`, `rsync`,
`policycoreutils-python-utils`).

#### 2. Storage

| Step | Detail |
|------|--------|
| Detect filesystem | Runs `blkid` on `bench_data_disk` to check if a filesystem exists. |
| Create XFS | Formats the disk with `mkfs.xfs` only when the disk is blank **and** `bench_storage_mkfs=true` in inventory. Fails if the disk is blank and `bench_storage_mkfs` is false, to avoid accidental data loss. |
| Mount | Mounts `bench_data_disk` at `bench_data_mount` (default `/pgsql`) with options from `bench_data_mount_opts`. |

Controlled by `bench_storage_enable` (default `true`). Set to `false` when
PGDATA lives on the root filesystem.

#### 3. Kernel tuning

- **sysctl**: Sets `vm.swappiness=1` and `kernel.numa_balancing=0` to reduce
  jitter during benchmark runs.  Controlled by `bench_sysctl_tuning`.
- **Transparent Huge Pages (THP)**: Installs a oneshot systemd unit
  (`disable-thp.service`) that writes `never` to both
  `/sys/kernel/mm/transparent_hugepage/enabled` and `defrag` before PostgreSQL
  starts. THP causes unpredictable latency spikes under database workloads.
  Controlled by `bench_disable_thp`.

#### 4. PostgreSQL from PGDG

Installs PostgreSQL from the upstream
[PGDG Yum repository](https://www.postgresql.org/download/linux/redhat/):

1. **Install PGDG repo RPM** — adds the `pgdg-redhat-repo-latest.noarch.rpm`
   for the detected EL major version.
2. **Disable built-in `postgresql` module** — on EL 8/9, the distro ships a
   DNF module that shadows PGDG packages; this step disables it. Skipped on
   EL 10+ (no modularity).
3. **Disable unused PGDG repos** — only the repo matching `postgresql_major`
   (e.g. `pgdg17`) stays enabled; all others (pgdg10–pgdg19 except the one in
   use) are disabled to speed up metadata fetches.
4. **Clean DNF metadata** — forces a fresh cache after repo changes.
5. **Install server + contrib** — `postgresql<major>-server` and
   `postgresql<major>-contrib`.
6. **Install `python3-psycopg2`** — required by the Ansible
   `community.postgresql` modules used later in the playbook.

#### 5. PostgreSQL data directory and service

| Step | Detail |
|------|--------|
| Set `PGDATA` | Writes `PGDATA=<pgsql_data_dir>` to `/etc/sysconfig/postgresql-<major>`. |
| Systemd drop-in | When PGDATA is outside `/var/lib/pgsql/`, creates a drop-in at `/etc/systemd/system/postgresql-<major>.service.d/pgdata.conf` so systemd passes the custom path to the service. |
| SELinux | Adds a `postgresql_db_t` file context for the custom data path and runs `restorecon`. Skipped if SELinux is disabled or PGDATA is under the default path. |
| initdb | Runs `initdb -D <pgsql_data_dir>` as the `postgres` user. Idempotent — skipped when `PG_VERSION` already exists. A stale (empty) PGDATA directory is removed first. |
| Start service | Enables and starts `postgresql-<major>.service`. |

#### 6. PostgreSQL configuration

- **`max_connections`** — set via `ALTER SYSTEM`; PostgreSQL is restarted if
  the value changed.
- **Superuser password** — set through local peer auth so no password is
  needed for the initial connection.
- **Benchmark role and database** — creates `pgsql_benchmark_user` with
  `pgsql_benchmark_password`, creates `pgsql_benchmark_db` owned by that user,
  grants `ALL PRIVILEGES` on the database and `CREATE ON SCHEMA public`
  (required since PostgreSQL 15 tightened default schema permissions).

#### 7. Network access

- **`listen_addresses`** — set to `pgsql_listen_addresses` (default `*`) via
  `ALTER SYSTEM`.
- **`pg_hba.conf`** — adds a `host all all <network> scram-sha-256` entry for
  the client subnet (`pgsql_benchmark_client_hba_network`).
- PostgreSQL is restarted if either setting changed, then the playbook
  verifies the port is reachable.

#### 8. Firewall

Opens `pgsql_port` (default 5432) in iptables with an `INPUT -p tcp --dport`
ACCEPT rule. Controlled by `bench_manage_firewall`.

#### 9. PCP monitoring

Installs `pcp`, `pcp-system-tools`, and `pcp-gui`; enables and starts `pmcd`.
A smoke test runs `pmprobe` against a small set of metrics
(`kernel.all.cpu.user`, `mem.util.used`, `disk.all.read`) to confirm PCP is
functional. Controlled by `pcp_capture_enable` and `pcp_setup_smoke_test`.

---

### Play 2 — Provision HammerDB load generator (`client`)

#### 1. Base packages

Installs `python3`, `python3-psycopg2`, `podman`, and `glibc-common`.

#### 2. HammerDB container

- Checks whether the image (`hammerdb_container_image`, default
  `docker.io/tpcorg/hammerdb:v4.12`) already exists locally via
  `podman image exists`.
- Pulls the image only when it is not present.
- Creates the work directory (`hammerdb_remote_workdir`, default
  `/root/hammerdb-run`).
- Templates three Tcl scripts into the work directory:
  - `setup.tcl` — TPC-C schema build
  - `run.tcl` — timed benchmark run
  - `cleanup.tcl` — drop schema
- Runs `hammerdbcli help` inside the container as a smoke test.

#### 3. PCP monitoring

Same as bench: installs PCP, starts `pmcd`, runs the smoke test.

#### 4. Connectivity verification

- **TCP check** — `wait_for` on `pgsql_host:pgsql_port` (bench address) from
  the client. If this fails, the rescue block prints troubleshooting steps
  (VPN, iptables, `listen_addresses`).
- **Auth check** — `postgresql_ping` authenticates to the benchmark database
  as `pgsql_benchmark_user`. Confirms end-to-end connectivity and credential
  correctness.

---

## Key inventory variables

All variables are set in `inventory.ini` under `[bench:vars]`, `[client:vars]`,
or `[remote:vars]`.

| Variable | Default | Purpose |
|----------|---------|---------|
| `postgresql_major` | `17` | PGDG PostgreSQL major version to install. |
| `bench_data_disk` | `/dev/nvme0n1` | Block device for PostgreSQL data. |
| `bench_data_mount` | `/pgsql` | Mount point for the data volume. |
| `bench_storage_mkfs` | `false` | Set `true` on first run to format a blank disk. |
| `bench_sysctl_tuning` | `true` | Apply sysctl tuning (swappiness, NUMA). |
| `bench_disable_thp` | `true` | Disable Transparent Huge Pages. |
| `bench_manage_firewall` | `true` | Open PostgreSQL port in iptables. |
| `pgsql_port` | `5432` | PostgreSQL listen port. |
| `pgsql_listen_addresses` | `*` | PostgreSQL `listen_addresses`. |
| `pgsql_max_connections` | `2000` | `max_connections` for benchmark load. |
| `pgsql_benchmark_user` | `benchuser` | Database role for HammerDB. |
| `pgsql_benchmark_db` | `benchdb` | Database name for TPC-C schema. |
| `hammerdb_container_image` | `docker.io/tpcorg/hammerdb:v4.12` | HammerDB OCI image. |
| `hammerdb_remote_workdir` | `/root/hammerdb-run` | Remote directory for Tcl scripts. |
| `pcp_capture_enable` | `true` | Install and enable PCP on both hosts. |

## setup.yaml vs setup_EL9.yaml

| | `setup.yaml` | `setup_EL9.yaml` |
|---|---|---|
| **Target** | RHEL 10.x, 9.7+, or any release where PGDG deps are satisfied by the base OS | RHEL 9.0 (and potentially other early 9.x minors before 9.7) |
| **PGDG deps workaround** | Not present — system libs are new enough | Included — pulls `openldap`, `openssl-libs`, `openssh-server/clients` from a newer compose via a temporary repo |
| **`$releasever` fix** | Not needed — DNF resolves `$releasever` correctly on latest releases | Two `sed` passes to replace `$releasever` with the EL major in PGDG repo files, because RHEL 9.0 resolves it to `9.0` instead of `9` |
| **Inventory extras** | None | Requires `pgdg_deps_baseurl` pointing to a RHEL 9.8+ compose BaseOS |

See [setup_EL9.md](setup_EL9.md) for details on the RHEL 9.0 workaround.
