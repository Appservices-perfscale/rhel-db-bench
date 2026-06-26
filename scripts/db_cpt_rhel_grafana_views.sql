-- Grafana-friendly views over db_cpt_rhel_data (master.json rows).
-- Apply after scripts/db_cpt_rhel_schema.sql, e.g.:
--
--   PGPASSWORD='...' psql -h HOST -U USER -d DATABASE -f scripts/db_cpt_rhel_grafana_views.sql
--
-- Import grafana/dashboards/db-cpt-rhel-overview.json and point the PostgreSQL
-- datasource at the same database (see docs/grafana.md).

DROP VIEW IF EXISTS db_cpt_rhel_runs_v;

CREATE VIEW db_cpt_rhel_runs_v AS
SELECT
    d.id AS row_id,
    d.datetime AS run_started,
    d.data->>'id' AS run_id,
    d.data->>'name' AS workload_name,
    d.data->>'result' AS pass_fail,
    upper(coalesce(nullif(d.data->'cpt_profile'->>'hardware', ''), 'unknown')) AS hardware,
    coalesce(nullif(d.data->'cpt_profile'->>'label', ''), '') AS profile_label,
    coalesce((d.data->'cpt_profile'->>'baseline')::boolean, false) AS is_baseline,
    coalesce((d.data->'cpt_profile'->>'establish_baseline_run')::boolean, false) AS establish_baseline_run,
    d.data->'results'->'bench'->>'rhel_release_id' AS bench_rhel_release,
    split_part(d.data->'results'->'bench'->>'rhel_release_id', '.', 1) AS bench_rhel_major,
    'RHEL' || split_part(d.data->'results'->'bench'->>'rhel_release_id', '.', 1) AS bench_rhel_family,
    d.data->'results'->'bench'->>'distribution' AS bench_distribution,
    d.data->'results'->'bench'->>'kernel' AS bench_kernel,
    d.data->'results'->'client'->>'rhel_release_id' AS client_rhel_release,
    d.data->'results'->'client'->>'distribution' AS client_distribution,
    (d.data->'results'->'results'->>'nopm')::numeric AS nopm,
    (d.data->'results'->'results'->>'tpm')::numeric AS tpm,
    d.data->'results'->'hammerdb'->>'virtual_users' AS virtual_users,
    d.data->'results'->'hammerdb'->>'warehouses' AS warehouses,
    d.data->'results'->>'benchmark_tuning_profile' AS tuning_profile,
    (d.data->'monitoring'->'bench'->>'avg_cpu_utilization_percent')::numeric AS bench_cpu_avg_pct,
    (d.data->'monitoring'->'bench'->>'p95_cpu_utilization_percent')::numeric AS bench_cpu_p95_pct,
    (d.data->'monitoring'->'bench'->>'max_cpu_utilization_percent')::numeric AS bench_cpu_max_pct,
    (d.data->'monitoring'->'bench'->>'avg_mem_used_gib')::numeric AS bench_mem_avg_gib,
    (d.data->'monitoring'->'bench'->>'p95_mem_used_gib')::numeric AS bench_mem_p95_gib,
    (d.data->'monitoring'->'bench'->>'avg_disk_read_kbps')::numeric AS bench_disk_read_avg_kbps,
    (d.data->'monitoring'->'bench'->>'avg_disk_write_kbps')::numeric AS bench_disk_write_avg_kbps,
    (d.data->'monitoring'->'bench'->>'avg_disk_total_kbps')::numeric AS bench_disk_total_avg_kbps,
    (d.data->'monitoring'->'bench'->>'avg_iops_estimated')::numeric AS bench_iops_avg,
    (d.data->'monitoring'->'bench'->>'avg_read_iops_estimated')::numeric AS bench_read_iops_avg,
    (d.data->'monitoring'->'bench'->>'avg_write_iops_estimated')::numeric AS bench_write_iops_avg,
    (
        SELECT coalesce(sum((value->>'avg_in_kbps')::numeric), 0)
        FROM jsonb_each(
            CASE
                WHEN jsonb_typeof(d.data->'monitoring'->'bench'->'network') = 'object'
                THEN d.data->'monitoring'->'bench'->'network'
                ELSE '{}'::jsonb
            END
        )
    ) AS bench_net_in_avg_kbps,
    (
        SELECT coalesce(sum((value->>'avg_out_kbps')::numeric), 0)
        FROM jsonb_each(
            CASE
                WHEN jsonb_typeof(d.data->'monitoring'->'bench'->'network') = 'object'
                THEN d.data->'monitoring'->'bench'->'network'
                ELSE '{}'::jsonb
            END
        )
    ) AS bench_net_out_avg_kbps,
    (d.data->'monitoring'->'client'->>'avg_cpu_utilization_percent')::numeric AS client_cpu_avg_pct,
    (d.data->'monitoring'->'client'->>'p95_cpu_utilization_percent')::numeric AS client_cpu_p95_pct,
    (d.data->'monitoring'->'client'->>'max_cpu_utilization_percent')::numeric AS client_cpu_max_pct,
    (d.data->'monitoring'->'client'->>'avg_mem_used_gib')::numeric AS client_mem_avg_gib,
    (d.data->'monitoring'->'client'->>'p95_mem_used_gib')::numeric AS client_mem_p95_gib,
    (d.data->'monitoring'->'client'->>'avg_disk_read_kbps')::numeric AS client_disk_read_avg_kbps,
    (d.data->'monitoring'->'client'->>'avg_disk_write_kbps')::numeric AS client_disk_write_avg_kbps,
    (d.data->'monitoring'->'client'->>'avg_disk_total_kbps')::numeric AS client_disk_total_avg_kbps,
    (d.data->'monitoring'->'client'->>'avg_iops_estimated')::numeric AS client_iops_avg,
    (
        SELECT coalesce(sum((value->>'avg_in_kbps')::numeric), 0)
        FROM jsonb_each(
            CASE
                WHEN jsonb_typeof(d.data->'monitoring'->'client'->'network') = 'object'
                THEN d.data->'monitoring'->'client'->'network'
                ELSE '{}'::jsonb
            END
        )
    ) AS client_net_in_avg_kbps,
    (
        SELECT coalesce(sum((value->>'avg_out_kbps')::numeric), 0)
        FROM jsonb_each(
            CASE
                WHEN jsonb_typeof(d.data->'monitoring'->'client'->'network') = 'object'
                THEN d.data->'monitoring'->'client'->'network'
                ELSE '{}'::jsonb
            END
        )
    ) AS client_net_out_avg_kbps
FROM db_cpt_rhel_data d;

CREATE INDEX IF NOT EXISTS db_cpt_rhel_data_hardware_idx
    ON db_cpt_rhel_data ((
        upper(coalesce(nullif(data->'cpt_profile'->>'hardware', ''), 'unknown'))
    ));

CREATE INDEX IF NOT EXISTS db_cpt_rhel_data_bench_rhel_major_idx
    ON db_cpt_rhel_data ((
        split_part(data->'results'->'bench'->>'rhel_release_id', '.', 1)
    ));
