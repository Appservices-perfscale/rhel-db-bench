-- DB-CPT-RHEL dedicated tables (do not modify any existing tables in shared databases).
-- Run once with your credentials, e.g.:
--
--   PGPASSWORD='...' psql -h HOST -U USER -d DATABASE -f scripts/db_cpt_rhel_schema.sql
--
-- Project "data" table (main JSON per run):
--   db_cpt_rhel_data — id, datetime (test started), data (JSONB master document)
--
-- pass_or_fail audit log:
--   db_cpt_rhel_decisions — per-metric decision records
--
-- Grafana (optional):
--   scripts/db_cpt_rhel_grafana_views.sql — db_cpt_rhel_runs_v view

CREATE TABLE IF NOT EXISTS db_cpt_rhel_data (
    id        SERIAL PRIMARY KEY,
    datetime  TIMESTAMPTZ NOT NULL,
    data      JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS db_cpt_rhel_data_name_idx
    ON db_cpt_rhel_data ((data->>'name'));

CREATE INDEX IF NOT EXISTS db_cpt_rhel_data_datetime_idx
    ON db_cpt_rhel_data (datetime DESC);

CREATE TABLE IF NOT EXISTS db_cpt_rhel_decisions (
    id         SERIAL PRIMARY KEY,
    data       JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
