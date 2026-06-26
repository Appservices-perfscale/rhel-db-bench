# Grafana dashboard — DB-CPT-RHEL regression (simple)

Minimal dashboard: **REGRESSION** (red) or **NO REGRESSION** (green) when comparing
a newer RHEL against an older baseline (same hardware + virtual users).

## Import

1. **Dashboards → New → Import**
2. Upload `grafana/dashboards/db-cpt-rhel-overview.json`
3. Map **DS_POSTGRESQL** to your PostgreSQL datasource
4. **Import** (overwrite if replacing an older version)

## Use

| Filter | Example |
|--------|---------|
| Baseline RHEL | `9.0` (reference) |
| Compare RHEL | `9.4` (under test) |
| Hardware | **All** if runs are `UNKNOWN` |
| Virtual users | **All** or `112` |

**Total runs** must be > 0. If zero, upload data first (see below).

## Upload data

```bash
export PGPASSWORD='...'
python3 scripts/upload_db_cpt_result.py results/.../YOUR-master.json \
  --host HOST --database DATABASE --user USER --table db_cpt_rhel_data
```

Each upload refreshes `db_cpt_rhel_runs_v` automatically. To update the view only
(e.g. after importing a new dashboard):

```bash
python3 scripts/upload_db_cpt_result.py --apply-grafana-views-only \
  --host HOST --database DATABASE --user USER
```

## Panels

| Panel | Meaning |
|-------|---------|
| **RHEL comparison by virtual users** | Side-by-side bars: NOPM, TPM, max CPU, and avg read IOPS vs VU for **Baseline RHEL** vs **Compare RHEL** (blue / red) |
| Big status | **REGRESSION** / **NO REGRESSION** / **NO DATA** |
| Latest on Compare RHEL | NOPM on newest run of version under test |
| Best on Baseline RHEL | Best NOPM on reference version |
| OPL | Pipeline pass/fail from `compare` runs (optional) |
| Table | Each RHEL row vs baseline |

Dashboard UID: `db-cpt-rhel-overview`
