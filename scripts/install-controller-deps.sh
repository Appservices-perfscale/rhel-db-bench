#!/usr/bin/env bash
# Install controller Python deps (OPL pass_or_fail + DB upload).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python3 -m pip install -r requirements.txt
# extras provides opl.http for pass_or_fail; --no-deps skips locust/gevent/kafka.
python3 -m pip install --no-deps \
  'git+https://github.com/redhat-performance/opl.git#subdirectory=extras'
