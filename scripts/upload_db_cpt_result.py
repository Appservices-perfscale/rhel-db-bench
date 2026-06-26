#!/usr/bin/env python3
"""Upload master.json into the DB-CPT-RHEL data table (id, datetime, data JSONB)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_SQL_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_GRAFANA_VIEWS_SQL = Path(__file__).resolve().parent / "db_cpt_rhel_grafana_views.sql"


def _started_timestamp(master: dict) -> datetime:
    started = master.get("started")
    if started:
        text = str(started).replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    timing = master.get("results", {}).get("timing", {})
    start_epoch = timing.get("start_epoch")
    if start_epoch not in (None, ""):
        return datetime.fromtimestamp(int(start_epoch), tz=timezone.utc)
    return datetime.now(tz=timezone.utc)


def _sql_statements(sql_path: Path) -> list[str]:
    lines: list[str] = []
    for line in sql_path.read_text(encoding="utf-8").splitlines():
        stripped = line.split("--", 1)[0]
        if stripped.strip():
            lines.append(stripped)
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]


def apply_grafana_views(connection) -> None:
    if not _GRAFANA_VIEWS_SQL.is_file():
        raise FileNotFoundError(f"Grafana views SQL not found: {_GRAFANA_VIEWS_SQL}")
    with connection.cursor() as cursor:
        for statement in _sql_statements(_GRAFANA_VIEWS_SQL):
            cursor.execute(statement)
    connection.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "master_json",
        nargs="?",
        type=Path,
        help="Path to <run>-master.json (omit with --apply-grafana-views-only)",
    )
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--table", default="db_cpt_rhel_data")
    parser.add_argument("--password-env", default="PGPASSWORD")
    parser.add_argument(
        "--apply-grafana-views-only",
        action="store_true",
        help="Refresh db_cpt_rhel_runs_v (and indexes) without uploading a run",
    )
    parser.add_argument(
        "--skip-grafana-views",
        action="store_true",
        help="Do not run scripts/db_cpt_rhel_grafana_views.sql after upload",
    )
    args = parser.parse_args()

    if args.apply_grafana_views_only:
        if args.master_json is not None:
            print("Do not pass master_json with --apply-grafana-views-only", file=sys.stderr)
            return 2
    elif args.master_json is None:
        print("master_json is required unless --apply-grafana-views-only is set", file=sys.stderr)
        return 2

    if not _SQL_IDENTIFIER.fullmatch(args.table):
        print(f"Invalid table name: {args.table!r}", file=sys.stderr)
        return 2

    password = os.environ.get(args.password_env)
    if password is None:
        print(f"Environment variable {args.password_env} is not set", file=sys.stderr)
        return 2

    try:
        import psycopg2
    except ImportError:
        print("psycopg2-binary is required: pip install psycopg2-binary", file=sys.stderr)
        return 2

    connection = psycopg2.connect(
        host=args.host,
        port=args.port,
        database=args.database,
        user=args.user,
        password=password,
    )
    try:
        if args.apply_grafana_views_only:
            apply_grafana_views(connection)
            print(f"Applied Grafana views from {_GRAFANA_VIEWS_SQL}")
            return 0

        master_path = args.master_json.expanduser().resolve()
        document = json.loads(master_path.read_text(encoding="utf-8"))
        run_datetime = _started_timestamp(document)

        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {args.table} (datetime, data) VALUES (%s, %s) RETURNING id",
                [run_datetime, json.dumps(document)],
            )
            row_id = cursor.fetchone()[0]
        connection.commit()

        if not args.skip_grafana_views:
            apply_grafana_views(connection)
            print(f"Applied Grafana views from {_GRAFANA_VIEWS_SQL}")

        print(
            f"Uploaded {master_path} to {args.table} id={row_id} "
            f"datetime={run_datetime.isoformat()}"
        )
    finally:
        connection.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
