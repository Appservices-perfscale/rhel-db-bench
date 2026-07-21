# CPT developer guide

How to seed a baseline and run regression checks without editing configs by hand
every time. For a short local how-to, start with the [root README](../README.md).

## One-time setup

1. Get hosts (`auto-schedule.yaml` or fill `inventory.local.ini`) and run
   `os-setup.yaml` + `setup.yaml` once.
2. Copy config templates (passwords stay gitignored):
   ```bash
   cp pass_or_fail_cfg.yaml.example pass_or_fail_cfg.yaml
   cp archive_cfg.yaml.example archive_cfg.yaml
   # Set postgresql.password in archive_cfg.yaml, or: export PGPASSWORD='...'
   ```
3. Create DB tables once: `psql … -f scripts/db_cpt_rhel_schema.sql`
4. Controller: `./scripts/install-controller-deps.sh` and `jq`

On Jenkins, set `CPT_ARTIFACT_ROOT` (e.g. `/workspace/ARTIFACTS/DB-CPT-RHEL`)
instead of relying on a local `artifact_storage.root`.

## What gets compared (CPT profile)

`pass_or_fail` matches history on these fields — **not** bench RHEL. So a
compare on 9.4 can use a 9.0 baseline when the rest of the profile matches:

| Field | How to set | Example |
|-------|------------|---------|
| Workload name | fixed in playbook | `DB-CPT-RHEL` |
| Virtual users | `--vu` or `hammerdb_virtual_users_matrix` | `112` |
| Hardware cohort | `--hardware` / `cpt_hardware_profile` | `r650` |
| Optional label | `--label` / `cpt_profile_label` | `staging` |

Method: `check_by_gte_min` — FAIL only if NOPM drops below the historical
minimum for that profile.

## Commands

### VU matrix in `inventory.ini`

```ini
hammerdb_virtual_users_matrix=112,224
hammerdb_matrix_run_count=1
```

`baseline` and `compare` loop that list by default:

`site.yml (vu=112) → cleanup → site.yml (vu=224) → cleanup`

One VU: `./scripts/cpt-run.sh compare --rhel 9.0 --vu 112`

### 1. Establish baseline

```bash
./scripts/cpt-run.sh baseline --rhel 9.0 --hardware r650
```

- Runs `site.yml` per VU, then `cleanup.yaml` (skip with `CPT_CLEANUP=false`)
- Skips regression check; sets `result: PASS`
- Uploads to PostgreSQL when `archive_cfg.yaml` is present

Do this once per profile (hardware + VU + label). Each VU gets its own baseline.

### 2. Compare

```bash
./scripts/cpt-run.sh compare --rhel 9.4 --hardware r650
```

Same VU sweep, but runs OPL `pass_or_fail` on each run. FAIL does not abort the
playbook.

Override VUs without editing inventory:

```bash
./scripts/cpt-run.sh baseline --rhel 9.0 --hardware r650 --vus 112,224
```

`matrix` is an alias for `compare`.

Handy flags: `--skip-os-setup`, `--skip-setup`, `--schedule`,
`--scalelab-cleanup`, `--workload-name`. Full list: `./scripts/cpt-run.sh --help`.

## Same thing with ansible-playbook

```bash
# Baseline
ansible-playbook playbooks/site.yml \
  -i inventory.ini -i inventory.local.ini \
  -e cpt_hardware_profile=r650 \
  -e cpt_establish_baseline=true

# Compare
ansible-playbook playbooks/site.yml \
  -i inventory.ini -i inventory.local.ini \
  -e cpt_hardware_profile=r650
```

`cpt-run.sh` skips `os-setup` when every bench host already reports the
requested `--rhel`. Use `--skip-os-setup` to force that, or change `--rhel` to
trigger distro-sync / Foreman rebuild.

## Changing RHEL on the bench

Same major (e.g. 9.4 → 9.7): `dnf distro-sync`. Different major (9 → 10):
Foreman wipe + Badfish PXE. If Foreman only has `RHEL 10.0` and you ask for
`10.2`, os-setup installs `10.0` then distro-syncs to `10.2`. Credentials come
from the QUADS assignment when you used `auto-schedule.yaml`.

```bash
./scripts/cpt-run.sh compare --rhel 10.2 --hardware r650
```

Optional dated compose after a RHEL 10 Foreman install:

```ini
# [bench:vars]
os_prep_rhel_compose_name=RHEL-10.2-20260408.1
```

```bash
ansible-playbook playbooks/os-setup.yaml -i inventory.ini -i inventory.local.ini \
  --limit bench -e bench_rhel_release_id=10.2
ansible-playbook playbooks/setup.yaml -i inventory.ini -i inventory.local.ini --limit bench
./scripts/cpt-run.sh compare --rhel 10.2 --hardware r650 --skip-os-setup --skip-setup
```

## Where to look at results

| Output | Location |
|--------|----------|
| NOPM / TPM | `results/…-results.json` |
| Pass/fail | `…-master.json` → `"result"` |
| PCP logs | Same dir, or `CPT_ARTIFACT_ROOT/<run_id>/` on Jenkins |
| Long-term KPI | PostgreSQL `db_cpt_rhel_data` |
| Decision audit | `db_cpt_rhel_decisions` |
| Grafana | [grafana.md](grafana.md) |

## Empty `result` on compare

No matching history for that profile (name + VU + hardware + label) →
`pass_or_fail` leaves `result` null. Run `baseline` first with the same
`--hardware` / `--label` / VU. Bench RHEL on the baseline can differ from the
compare run.

## Between runs

`cpt-run.sh` runs `cleanup.yaml` after each `site.yml` by default. Manual:

```bash
ansible-playbook playbooks/cleanup.yaml -i inventory.ini -i inventory.local.ini
```
