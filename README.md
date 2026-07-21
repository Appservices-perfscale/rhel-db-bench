# DB-CPT-RHEL

Compare PostgreSQL TPC-C performance across RHEL releases on bare metal.

You need two hosts on the **same rack**: **bench** (PostgreSQL) and **client**
(HammerDB). The main number you care about is **NOPM**.

This README is the local how-to. Deeper detail lives under [`docs/`](docs/).

---

## What you need on your laptop

- Ansible 2.14+ and collections: `ansible-galaxy install -r requirements.yml`
- Python deps: `./scripts/install-controller-deps.sh` (or see [`docs/container.md`](docs/container.md))
- `jq`
- Two RHEL hosts reachable over SSH (ScaleLab or your own)

Or skip the local install and use the controller container — see
[`docs/container.md`](docs/container.md).

---

## One-time config

```bash
# Hosts + SSH (or let auto-schedule write this for you)
cp inventory.local.ini.example inventory.local.ini
# Edit: ansible_host + ansible_ssh_pass for bench and client

# Optional — ScaleLab auto-schedule / major OS rebuilds
cp quads_cfg.yaml.example quads_cfg.yaml
# Fill quads_* and scalelab_ssh_pass

# Optional — pass/fail + upload to PostgreSQL
cp pass_or_fail_cfg.yaml.example pass_or_fail_cfg.yaml
cp archive_cfg.yaml.example archive_cfg.yaml
export PGPASSWORD='...'   # or set postgresql.password in archive_cfg.yaml
```

Create DB tables once (if you upload results):

```bash
psql -h HOST -U USER -d DATABASE -f scripts/db_cpt_rhel_schema.sql
```

VU list and other knobs live in `inventory.ini` (`hammerdb_virtual_users_matrix`, etc.).

---

## Easiest path: `cpt-run.sh`

This is the usual local entrypoint. It runs os-setup → setup → benchmark → cleanup
for each VU in your matrix.

```bash
# Seed baselines (sets result=PASS, skips regression check)
./scripts/cpt-run.sh baseline --rhel 9.0 --hardware r650

# Compare a later RHEL against history
./scripts/cpt-run.sh compare --rhel 9.4 --hardware r650

# Hosts already on the right OS / already provisioned
./scripts/cpt-run.sh compare --rhel 9.4 --hardware r650 \
  --skip-os-setup --skip-setup

# One VU only
./scripts/cpt-run.sh compare --rhel 9.4 --hardware r650 --vu 112
```

### Reserve ScaleLab hosts, run, release

Needs a filled-in `quads_cfg.yaml`:

```bash
./scripts/cpt-run.sh compare --rhel 9.4 --hardware r650 \
  --schedule --workload-name 'DB-CPT local' --scalelab-cleanup
```

That schedules two same-rack hosts, waits until SSH works, runs the pipeline,
then releases the assignment.

---

## Step by step (without the wrapper)

```bash
# 1. (Optional) reserve hosts
ansible-playbook playbooks/auto-schedule.yaml \
  -e "workload_name='DB-CPT local'"

# 2. Wait until SSH works (after schedule; can take a while)
ansible-playbook playbooks/wait-for-scalelab-hosts.yaml \
  -i inventory.ini -i inventory.local.ini

# 3. Pin RHEL compose / major rebuild if needed
ansible-playbook playbooks/os-setup.yaml \
  -i inventory.ini -i inventory.local.ini

# 4. Install PostgreSQL, HammerDB, PCP (once per host image)
ansible-playbook playbooks/setup.yaml \
  -i inventory.ini -i inventory.local.ini

# 5. Run a benchmark
ansible-playbook playbooks/site.yml \
  -i inventory.ini -i inventory.local.ini \
  -e cpt_hardware_profile=r650

# 6. Reset between runs
ansible-playbook playbooks/cleanup.yaml \
  -i inventory.ini -i inventory.local.ini

# 7. Give hosts back
ansible-playbook playbooks/scalelab-cleanup.yaml
```

Baseline seed (no regression check):

```bash
ansible-playbook playbooks/site.yml \
  -i inventory.ini -i inventory.local.ini \
  -e cpt_hardware_profile=r650 \
  -e cpt_establish_baseline=true
```

---

## Where results land

| What | Where |
|------|--------|
| NOPM / TPM / master JSON | `results/` |
| Pass/fail | `*-master.json` → `"result"` |
| Long-term store | PostgreSQL `db_cpt_rhel_data` (if `archive_cfg.yaml` is set) |
| Charts | Grafana — see [`docs/grafana.md`](docs/grafana.md) |

If `compare` leaves `result` empty, you probably have no baseline yet for that
profile (same workload name + VU + hardware + label). Run `baseline` first.

---

## More detail

| Doc | When you need it |
|-----|------------------|
| [docs/cpt-developer.md](docs/cpt-developer.md) | Baselines, profiles, `cpt-run.sh` flags |
| [docs/os-setup.md](docs/os-setup.md) | Distro-sync vs Foreman major rebuild |
| [docs/auto-schedule.md](docs/auto-schedule.md) | QUADS scheduling |
| [docs/scalelab-cleanup.md](docs/scalelab-cleanup.md) | Releasing hosts |
| [docs/setup.md](docs/setup.md) / [setup_EL9.md](docs/setup_EL9.md) | What provisioning installs |
| [docs/site.md](docs/site.md) / [test-logic.md](docs/test-logic.md) | Benchmark + master JSON |
| [docs/cleanup.md](docs/cleanup.md) | Reset between runs |
| [docs/grafana.md](docs/grafana.md) | Dashboard import |
| [docs/container.md](docs/container.md) | Image build, Quay, Jenkins secrets, Konflux |
