# CPT developer guide

How to seed a baseline and run regression checks without hand-editing configs each time.

## One-time setup

1. Reserve hosts (`auto-schedule.yaml`) and run `os-setup.yaml` + `setup.yaml` once.
2. Copy config templates and set DB credentials:
   ```bash
   cp pass_or_fail_cfg.yaml.example pass_or_fail_cfg.yaml
   cp archive_cfg.yaml.example archive_cfg.yaml
   export PGPASSWORD='...'
   ```
3. Create DB tables: `psql ... -f scripts/db_cpt_rhel_schema.sql`
4. Controller: `pip install -r requirements.txt` and `jq`.

On Jenkins, set `CPT_ARTIFACT_ROOT=/workspace/ARTIFACTS/DB-CPT-RHEL` (or `${JOB_NAME}`) instead of a local archive path.

## CPT profile (what gets compared)

`pass_or_fail` history matching uses fields stored on each `master.json` (bench
RHEL is **not** part of the match — compare on 9.4 uses baseline history from 9.0
when other profile fields align):

| Field | How to set | Example |
|-------|------------|---------|
| Workload name | fixed in playbook | `DB-CPT-RHEL` |
| Virtual users | `--vu` or first entry in `hammerdb_virtual_users_matrix` | `112` |
| Hardware cohort | `--hardware` / `cpt_hardware_profile` | `r650`, `r640` |
| Optional label | `--label` / `cpt_profile_label` | `staging` |

Runs with the **same profile** are compared for NOPM regression (`check_by_gte_min`: fail only if NOPM drops below historical minimum across prior runs, any bench RHEL).

## Commands

### VU list in `inventory.ini`

```ini
hammerdb_virtual_users_matrix=112,224
hammerdb_matrix_run_count=1
```

`baseline` and `compare` **loop this list by default**:

`site.yml (vu=112) → cleanup → site.yml (vu=224) → cleanup`

Single VU only: `./scripts/cpt-run.sh compare --rhel 9.0 --vu 112`

### 1. Establish baseline

```bash
./scripts/cpt-run.sh baseline --rhel 9.0
```

- Runs `site.yml` per VU in the matrix, then `cleanup.yaml` after each (skip: `CPT_CLEANUP=false`).
- Skips regression check; sets `result: PASS` on each run.
- Uploads to PostgreSQL (if `archive_cfg.yaml` exists).

Do this once per profile (RHEL + hardware + VU + label). With a matrix, each VU gets its own baseline in PostgreSQL.

### 2. Compare / regression check

```bash
./scripts/cpt-run.sh compare --rhel 9.4
```

- Same VU sweep, but runs OPL `pass_or_fail` on each run.
- Sets `result: PASS` or `FAIL` per VU (playbook does not abort on FAIL).

Override VU list without editing inventory:

```bash
./scripts/cpt-run.sh baseline --rhel 9.0 --vus 112,224
```

`matrix` is an alias for `compare` (same VU sweep).

## Equivalent ansible-playbook invocations

```bash
# Baseline
ansible-playbook playbooks/site.yml \
  -e os_prep_rhel_release_id=9.0 \
  -e cpt_hardware_profile=r650 \
  -e cpt_establish_baseline=true

# Compare
ansible-playbook playbooks/site.yml \
  -e os_prep_rhel_release_id=9.4 \
  -e cpt_hardware_profile=r650
```

`cpt-run.sh` skips `os-setup.yaml` automatically when every `[bench]` host
already reports the requested `--rhel` in `rpm -q redhat-release`. Use
`--skip-os-setup` to force-skip, or change `--rhel` to trigger a distro-sync.

## Changing RHEL on the bench

If you change `--rhel` to a version not yet installed on the bench host:

```bash
ansible-playbook playbooks/os-setup.yaml -i inventory.ini -i inventory.local.ini \
  -e os_prep_rhel_release_id=9.4
ansible-playbook playbooks/setup.yaml   # if PG/packages need refresh
./scripts/cpt-run.sh compare --rhel 9.4 --hardware r650
```

## Where to view results

| Output | Location |
|--------|----------|
| NOPM / TPM | `results/<RHEL>_<run_id>-results.json` |
| Pass/fail | `results/.../<run_id>-master.json` → `"result"` |
| Raw PCP logs | Same directory, or Jenkins `CPT_ARTIFACT_ROOT/<run_id>/` |
| Long-term KPI | PostgreSQL `db_cpt_rhel_data` |
| Decision audit | PostgreSQL `db_cpt_rhel_decisions` |
| Grafana dashboard | Import `grafana/dashboards/db-cpt-rhel-overview.json` — see [grafana.md](grafana.md) |

## Empty history on compare

If you run `compare` before any `baseline` for that profile (same name, VU,
hardware, and label), `pass_or_fail` has nothing to compare and `result` stays
`null`. The playbook prints a hint to run `baseline` first with matching flags
(RHEL on the baseline run can differ from the compare run).

## Between runs

`cpt-run.sh` runs `cleanup.yaml` after each `site.yml` by default, so the next
invocation starts from a clean database. To run cleanup manually:

```bash
ansible-playbook playbooks/cleanup.yaml
```
