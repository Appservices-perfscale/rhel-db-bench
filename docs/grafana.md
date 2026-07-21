# Grafana dashboard

Side-by-side comparison of CPT runs across two bench RHEL versions.

## Import

1. **Dashboards → New → Import**
2. Upload `grafana/dashboards/db-cpt-rhel-overview.json`
3. Point it at your PostgreSQL datasource (export UID is `dekgyz85doxdse`; remap if needed)
4. Import (overwrite if replacing an older board)

Apply the view if you have not yet:

```bash
psql -h HOST -U USER -d DATABASE -f scripts/db_cpt_rhel_grafana_views.sql
```

## Filters

| Filter | Role |
|--------|------|
| **Baseline RHEL** | Reference bench release (e.g. `9.0`) |
| **Compare RHEL** | Release under test (e.g. `9.7`) |
| **Virtual users** | Load level(s); **All** = one series per VU |
| **Hardware** | Cohort (`R650`, …) |
| **RHEL family** | `RHEL9`, `RHEL10`, … |

## What you see

| Section | Content |
|---------|---------|
| **Results over time** | NOPM, TPM, pass/fail vs time (per VU) |
| **Latest by VU** | Bar charts: newest run per VU per version |
| **SUT / client** | PCP CPU, memory, disk, net from archived runs |
| **Run history** | Full table including empty/`FAIL`/`PASS` results |

## Getting data in

`site.yml` uploads automatically when `archive_cfg.yaml` + `PGPASSWORD` are set.
Manual upload:

```bash
export PGPASSWORD='...'
python3 scripts/upload_db_cpt_result.py results/.../YOUR-master.json \
  --host HOST --database DATABASE --user USER --table db_cpt_rhel_data
```

Each upload refreshes `db_cpt_rhel_runs_v`. View only:

```bash
python3 scripts/upload_db_cpt_result.py --apply-grafana-views-only \
  --host HOST --database DATABASE --user USER
```

If pass/fail shows blank in Grafana, the master row’s `"result"` is null — usually
no baseline for that profile yet. See [cpt-developer.md](cpt-developer.md).
