#!/usr/bin/env bash
# Build-time sanity checks for the DB-CPT-RHEL controller image.
set -euo pipefail

ROOT="${DB_CPT_RHEL_ROOT:-/opt/db-cpt-rhel}"
cd "$ROOT"

echo "==> Tool versions"
ansible --version
ansible-playbook --version
python3 --version
jq --version
git --version
ssh -V 2>&1 | head -1

echo "==> Python dependencies"
python3 -c "import psycopg2; print('psycopg2', psycopg2.__version__)"
pass_or_fail.py --help >/dev/null

echo "==> Ansible Galaxy collections"
ansible-galaxy collection list -p ./collections | grep -E 'community\.(postgresql|general)|ansible\.posix'

echo "==> Entrypoint"
test -x ./scripts/cpt-run.sh
{ ./scripts/cpt-run.sh 2>&1 || true; } | grep -q 'Usage:'

echo "==> Playbook syntax-check"
cp inventory.local.ini.example inventory.local.ini
cp quads_cfg.yaml.example quads_cfg.yaml
trap 'rm -f inventory.local.ini quads_cfg.yaml' EXIT

INVENTORY=(-i inventory.ini -i inventory.local.ini)
PLAYBOOKS=(
  playbooks/auto-schedule.yaml
  playbooks/os-setup.yaml
  playbooks/setup.yaml
  playbooks/setup_EL9.yaml
  playbooks/test.yaml
  playbooks/site.yml
  playbooks/cleanup.yaml
  playbooks/scalelab-cleanup.yaml
)

for playbook in "${PLAYBOOKS[@]}"; do
  echo "  syntax-check: $playbook"
  ansible-playbook --syntax-check "${INVENTORY[@]}" "$playbook"
done

echo "==> Sanity checks passed"
