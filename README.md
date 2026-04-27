# Postgres-perf-comp

Ansible automation: **PostgreSQL** on a **bench** host, **HammerDB TPC-C** (Podman) on a **client** host, optional **PCP** metrics, then teardown and a **JSON benchmark report** on your controller.

## Prerequisites

- **Ansible** 2.14+ and **Python 3** on the controller (report merge uses `scripts/pcp_metrics_log_to_json.py`)
- SSH from the controller to **both** `bench` and `client` (become/root as configured)
- **dnf**-based bench and client hosts; **PostgreSQL** must already be installed and running on the bench host (match `inventory.ini` paths/packages). **Podman** on the client for HammerDB

## Quick start

1. From the repo root (uses `ansible.cfg`):

   ```bash
   ansible-galaxy collection install -r requirements.yml
   ```

2. **Inventory**: edit `inventory.ini` — `[bench]` is the DB host, `[client]` runs HammerDB; set `ansible_host`, users, and SSH auth. To keep secrets out of git, copy `inventory.local.ini.example` to **`inventory.local.ini`** (ignored by git); Ansible merges it with `inventory.ini`.

3. **Run** the full flow (prep DB → load test → drop DB → write report under `results/`):

   ```bash
   ansible-playbook playbooks/site.yml
   ```

   Optional `-e` overrides (see `[all:vars]` in `inventory.ini`): `perf_results_path`, `perf_run_stamp`, HammerDB knobs, `hammerdb_virtual_users`, `pcp_capture_enable`, etc.

**Matrix (repeat over VU list):** `ansible-playbook playbooks/run-matrix.yaml` — optional `-e 'virtual_users_list=[28,56]'` `-e run_count=2` `-e matrix_results_subdir=results/my-run`. Uses `inventory.local.ini` automatically if present.

**Backup tuned GUCs before changes:** `ansible-playbook playbooks/backup-pgsql-settings.yml`

## Artifacts

Under `results/` (or `perf_results_path`): `<run-id>-benchmark-report.json`, host/client `*-metrics-samples.log` when PCP is enabled. `collections/` holds Galaxy collections; `./.ansible/tmp` is local temp.

HammerDB TCL comes from **`templates/*.j2`** → `hammerdb_remote_workdir` on the client (default `/root/hammerdb-run`).
