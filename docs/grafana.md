# Grafana dashboard — DB-CPT-RHEL

Compare CPT benchmark results across two RHEL bench versions (Pipelines-style layout).

## Import

1. **Dashboards → New → Import**
2. Upload `grafana/dashboards/db-cpt-rhel-overview.json`
3. Map the PostgreSQL datasource (UID `dekgyz85doxdse` in the export, or your own)
4. **Import** (overwrite if replacing an older version)

## Filters

| Filter | Role |
|--------|------|
| **Baseline RHEL (version 1)** | Reference bench release (e.g. `9.0`) |
| **Compare RHEL (version 2)** | Release under test (e.g. `9.7`) |
| **Virtual users** | Load level(s); **All** shows every VU as a separate series |
| **Hardware** | Bench hardware profile (`R650`, etc.) |
| **RHEL family** | `RHEL9`, `RHEL8`, … |

## Layout (top to bottom)

| Section | What it shows |
|---------|----------------|
| **Benchmark results over time** | Side-by-side time series per version: NOPM, TPM, pass/fail (one line per VU) — same pattern as the Pipelines Performance Comparison dashboard |
| **RHEL comparison by virtual users (latest run per VU)** | Bar charts using `DISTINCT ON (virtual_users, bench_rhel_release)` to pick the **most recent run** per VU per version |
| **PostgreSQL SUT / HammerDB client** | PCP monitoring from archived runs |
| **Pass / fail and run history** | Aggregate results and full run table |

## Upload data

```bash
export PGPASSWORD='...'
python3 scripts/upload_db_cpt_result.py results/.../YOUR-master.json \
  --host HOST --database DATABASE --user USER --table db_cpt_rhel_data
```

Each upload refreshes `db_cpt_rhel_runs_v` automatically. To update the view only:

```bash
python3 scripts/upload_db_cpt_result.py --apply-grafana-views-only \
  --host HOST --database DATABASE --user USER
```

Dashboard UID: `db-cpt-rhel`
