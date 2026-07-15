#!/usr/bin/env bash
# DB-CPT-RHEL single-shot entrypoint — runs the full benchmark pipeline:
#   [auto-schedule → wait-for-hosts →] os-setup → setup → site.yml (per VU) → cleanup [→ scalelab-cleanup]
#
# Prerequisites: inventory.local.ini with bench/client hosts (or --schedule to create it),
# pass_or_fail_cfg.yaml + archive_cfg.yaml (or CPT_ARTIFACT_ROOT on Jenkins), PGPASSWORD set.
#
# Examples:
#   ./scripts/cpt-run.sh baseline --rhel 9.0
#   ./scripts/cpt-run.sh compare  --rhel 9.4
#   ./scripts/cpt-run.sh compare  --rhel 9.0 --vu 112
#   ./scripts/cpt-run.sh matrix   --rhel 9.0
#   ./scripts/cpt-run.sh baseline --rhel 9.0 --skip-os-setup --skip-setup
#
# End-to-end (schedule → benchmark → release):
#   ./scripts/cpt-run.sh compare --rhel 9.4 --hardware r650 \
#     --schedule --workload-name 'DB-CPT nightly' --scalelab-cleanup

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MODE=""
RHEL=""
HARDWARE=""
LABEL=""
VU=""
VUS=""
REPEATS=""
CPT_CLEANUP="${CPT_CLEANUP:-${MATRIX_CLEANUP:-true}}"
SKIP_OS_SETUP="${SKIP_OS_SETUP:-false}"
SKIP_SETUP="${SKIP_SETUP:-false}"
INVENTORY_LOCAL="${INVENTORY_LOCAL:-inventory.local.ini}"
DO_SCHEDULE="${DO_SCHEDULE:-false}"
WORKLOAD_NAME="${WORKLOAD_NAME:-}"
DO_SCALELAB_CLEANUP="${DO_SCALELAB_CLEANUP:-false}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-5400}"
EXTRA=()
ANSIBLE_ARGS=()

# ── Pipeline stage runners ──────────────────────────────────────────────

run_schedule() {
  if [[ "$DO_SCHEDULE" != "true" ]]; then
    return 0
  fi
  if [[ -z "$WORKLOAD_NAME" ]]; then
    echo "error: --workload-name is required when using --schedule" >&2
    exit 1
  fi
  echo "---------- Auto-schedule: reserving ScaleLab hosts ----------"
  ansible-playbook playbooks/auto-schedule.yaml \
    -e "workload_name=${WORKLOAD_NAME}" \
    "${EXTRA[@]}"

  echo "---------- Waiting for hosts (timeout ${WAIT_TIMEOUT}s) ----------"
  ansible-playbook playbooks/wait-for-scalelab-hosts.yaml \
    -i inventory.ini -i "$INVENTORY_LOCAL" \
    -e "wait_ssh_timeout=${WAIT_TIMEOUT}" \
    "${EXTRA[@]}"
  echo "---------- All hosts SSH-reachable ----------"
}

run_scalelab_cleanup() {
  if [[ "$DO_SCALELAB_CLEANUP" != "true" ]]; then
    return 0
  fi
  echo "---------- ScaleLab cleanup: releasing hosts ----------"
  ansible-playbook playbooks/scalelab-cleanup.yaml "${EXTRA[@]}" || true
}

run_os_setup() {
  if [[ "$SKIP_OS_SETUP" == "true" ]]; then
    echo "---------- Skipping os-setup (--skip-os-setup) ----------"
    return 0
  fi
  if bench_hosts_already_on_rhel; then
    echo "---------- Skipping os-setup (bench already on RHEL ${RHEL}) ----------"
    return 0
  fi
  echo "---------- OS Setup: bench → RHEL ${RHEL} (${RHEL_RELEASE_ROOT}) ----------"
  ansible-playbook playbooks/os-setup.yaml "${ANSIBLE_ARGS[@]}" "${EXTRA[@]}"
}

run_setup() {
  if [[ "$SKIP_SETUP" == "true" ]]; then
    echo "---------- Skipping setup (--skip-setup) ----------"
    return 0
  fi
  echo "---------- Provisioning (setup.yaml) ----------"
  if ansible-playbook playbooks/setup.yaml "${ANSIBLE_ARGS[@]}" "${EXTRA[@]}"; then
    return 0
  fi
  echo "---------- setup.yaml failed — falling back to setup_EL9.yaml ----------"
  ansible-playbook playbooks/setup_EL9.yaml "${ANSIBLE_ARGS[@]}" "${EXTRA[@]}"
}

run_cleanup() {
  echo "---------- Cleanup after benchmark ----------"
  ansible-playbook playbooks/cleanup.yaml "${ANSIBLE_ARGS[@]}" "${EXTRA[@]}"
}

run_site() {
  local vu="$1"
  local run_ts="$2"
  local label="${3:-}"
  echo "---------- Benchmark VU=${vu} ${label}----------"
  local site_args=(
    "${ANSIBLE_ARGS[@]}"
    -e "hammerdb_virtual_users=${vu}"
    -e "perf_run_stamp=${run_ts}"
  )
  ansible-playbook playbooks/site.yml "${site_args[@]}" "${EXTRA[@]}"
}

# ── VU sweep (matrix / multi-VU) ───────────────────────────────────────

run_vu_sweep() {
  local sweep_label="$1"
  [[ -z "$VUS" ]] && VUS="$(inventory_var hammerdb_virtual_users_matrix)"
  if [[ -z "$VUS" ]]; then
    echo "error: set hammerdb_virtual_users_matrix in inventory.ini or pass --vus" >&2
    exit 1
  fi
  [[ -z "$REPEATS" ]] && REPEATS="$(inventory_var hammerdb_matrix_run_count 1)"
  REPEATS="${REPEATS:-1}"

  echo "==> DB-CPT-RHEL ${sweep_label}"
  echo "    RHEL bench: ${RHEL} (${RHEL_RELEASE_ROOT})"
  [[ -n "$HARDWARE" ]] && echo "    hardware:   ${HARDWARE}"
  [[ -n "$LABEL" ]] && echo "    label:      ${LABEL}"
  echo "    VUs:        ${VUS}"
  echo "    repeats:    ${REPEATS}"
  echo "    cleanup:    ${CPT_CLEANUP} (after each site.yml)"
  echo

  IFS=',' read -r -a _VU_LIST <<< "$VUS"
  local _vu_trimmed=()
  local _vu_raw _vu
  for _vu_raw in "${_VU_LIST[@]}"; do
    _vu="$(printf '%s' "$_vu_raw" | tr -d ' ')"
    [[ -n "$_vu" ]] && _vu_trimmed+=("$_vu")
  done

  local total=$(( ${#_vu_trimmed[@]} * REPEATS ))
  local ok=0 iter=0
  local failed=()
  local _run run_ts

  for _vu in "${_vu_trimmed[@]}"; do
    for (( _run = 1; _run <= REPEATS; _run++ )); do
      iter=$((iter + 1))
      run_ts="$(date +%Y%m%d-%H%M%S)"
      echo "========== VU=${_vu} repeat=${_run} stamp=${run_ts} (${iter}/${total}) =========="

      if ! run_site "$_vu" "$run_ts" "(repeat ${_run})"; then
        failed+=("vu${_vu}_run${_run}")
        continue
      fi
      ok=$((ok + 1))

      if [[ "$CPT_CLEANUP" == "true" ]]; then
        if ! run_cleanup; then
          failed+=("vu${_vu}_run${_run} (cleanup)")
          break 2
        fi
      fi
    done
  done

  echo
  echo "${sweep_label} complete: ${ok} of ${total} runs succeeded."
  if [[ ${#failed[@]} -gt 0 ]]; then
    echo "Failed: ${failed[*]}"
    exit 1
  fi
}

# ── Usage ───────────────────────────────────────────────────────────────

usage() {
  cat <<'EOF'
Usage:
  cpt-run.sh baseline [options] [-- extra ansible-playbook args...]
  cpt-run.sh compare  [options] [-- extra ansible-playbook args...]
  cpt-run.sh matrix   [options] [-- extra ansible-playbook args...]

Full pipeline (per invocation):
  0. auto-schedule      — (optional) reserve ScaleLab hosts via QUADS + wait for SSH
  1. os-setup.yaml      — pin RHEL, distro-sync, reboot, write facts.json
                          (skipped when bench already reports --rhel, or --skip-os-setup)
  2. setup.yaml         — provision bench + client (fallback: setup_EL9.yaml)
  3. site.yml (per VU)  — benchmark + master JSON + pass/fail + upload
  4. cleanup.yaml       — reset benchmark state between VU iterations
  5. scalelab-cleanup   — (optional) release ScaleLab hosts

Modes:
  baseline  For each VU in hammerdb_virtual_users_matrix: site.yml (seed baseline), cleanup.yaml.
  compare   Same VU sweep with pass_or_fail regression check on each run.
  matrix    Alias for compare (VU sweep from inventory).

Without --vu, all modes loop hammerdb_virtual_users_matrix from inventory.ini
(e.g. 112,224 → site vu=112 → cleanup → site vu=224 → cleanup).
Use --vu N for a single benchmark only.

Options (--rhel is required):
  --rhel VERSION       Bench RHEL for os-setup + benchmark (e.g. 9.0, 9.4, 10.2); compose mirror path follows major
  --hardware PROFILE   Hardware cohort tag (r640, r650, …) stored in master cpt_profile
  --label TEXT         Optional free-form profile label (e.g. team-a, staging)
  --vu N               Single VU only (skip matrix sweep)
  --vus LIST           Override matrix VU list, e.g. 112,224
  --repeats N          Repeats per VU (default: inventory hammerdb_matrix_run_count or 1)
  --skip-os-setup      Force-skip os-setup.yaml (bench hosts already pinned to correct RHEL)
  --skip-setup         Skip setup.yaml (bench + client already provisioned)

ScaleLab scheduling:
  --schedule           Run auto-schedule.yaml before the pipeline (requires quads_cfg.yaml)
  --workload-name TEXT QUADS assignment description (required with --schedule)
  --scalelab-cleanup   Run scalelab-cleanup.yaml after the pipeline (releases hosts)
  --wait-timeout SECS  Max seconds to wait for hosts after scheduling (default: 5400 = 90 min)

  -h, --help           Show this help

Environment:
  PGPASSWORD           PostgreSQL password for pass_or_fail and upload
  CPT_ARTIFACT_ROOT    Jenkins artifact directory (optional; see archive_cfg.yaml.example)
  CPT_CLEANUP          Run cleanup.yaml after each site.yml (default: true)
  SKIP_OS_SETUP        Same as --skip-os-setup (default: false)
  SKIP_SETUP           Same as --skip-setup (default: false)
  DO_SCHEDULE          Same as --schedule (default: false)
  DO_SCALELAB_CLEANUP  Same as --scalelab-cleanup (default: false)
  WORKLOAD_NAME        Same as --workload-name
  WAIT_TIMEOUT         Same as --wait-timeout (default: 5400)

Examples:
  ./scripts/cpt-run.sh baseline --rhel 9.0
  ./scripts/cpt-run.sh compare  --rhel 9.4
  ./scripts/cpt-run.sh compare  --rhel 9.0 --vu 112
  ./scripts/cpt-run.sh baseline --rhel 9.0 --vus 112,224
  ./scripts/cpt-run.sh baseline --rhel 9.0 --skip-os-setup --skip-setup

  # End-to-end: schedule hosts, benchmark, release hosts
  ./scripts/cpt-run.sh compare --rhel 9.4 --hardware r650 \
    --schedule --workload-name 'DB-CPT nightly' --scalelab-cleanup
EOF
}

# ── Helpers ─────────────────────────────────────────────────────────────

inventory_var() {
  local key="$1"
  local default="${2:-}"
  local line
  line="$(grep -E "^${key}=" "$ROOT/inventory.ini" 2>/dev/null | head -1 || true)"
  if [[ -z "$line" ]]; then
    printf '%s' "$default"
    return
  fi
  printf '%s' "${line#*=}" | tr -d ' '
}

rhel_release_root() {
  local id="${1:?release id required}"
  local major="${id%%.*}" prefix mirror compose_name
  prefix="$(inventory_var os_prep_rhel_release_root_prefix)"
  compose_name="$(inventory_var os_prep_rhel_compose_name)"
  if [[ -n "$prefix" ]]; then
    if [[ -n "$compose_name" ]]; then
      printf '%s/%s' "${prefix%/}" "$compose_name"
    else
      printf '%s/latest-RHEL-%s.0' "${prefix%/}" "$id"
    fi
    return
  fi
  mirror="$(inventory_var os_prep_rhel_release_mirror "https://download.eng.pnq.redhat.com")"
  if [[ -z "$mirror" ]]; then
    echo "error: set os_prep_rhel_release_mirror in inventory.ini [remote:vars]" >&2
    exit 1
  fi
  if [[ -n "$compose_name" ]]; then
    printf '%s/rhel-%s/rel-eng/RHEL-%s/%s' "${mirror%/}" "$major" "$major" "$compose_name"
  else
    printf '%s/rhel-%s/rel-eng/RHEL-%s/latest-RHEL-%s.0' \
      "${mirror%/}" "$major" "$major" "$id"
  fi
}

# Return 0 when every [bench] host already reports the requested RHEL release
# (same check os-setup.yaml uses: release id appears in rpm -q redhat-release).
bench_hosts_already_on_rhel() {
  local out line rel found=0
  if ! out="$(ansible bench "${ANSIBLE_ARGS[@]}" -m ansible.builtin.command \
      -a "rpm -q redhat-release" 2>&1)"; then
    return 1
  fi
  if grep -qE ' UNREACHABLE!| FAILED! ' <<< "$out"; then
    return 1
  fi

  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    case "$line" in
      *"(stdout)"*)
        # oneline callback: host | CHANGED | rc=0 | (stdout) redhat-release-9.4-...
        rel="${line##*"(stdout) "}"
        ;;
      *">>"*)
        # default callback: host | CHANGED | rc=0 >>  then stdout on the next line
        if ! IFS= read -r rel; then
          return 1
        fi
        ;;
      *)
        continue
        ;;
    esac
    rel="${rel//$'\r'/}"
    found=1
    if [[ "$rel" != *"${RHEL}"* ]]; then
      return 1
    fi
  done <<< "$out"

  [[ "$found" -eq 1 ]]
}

# ── Argument parsing ────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
  case "$1" in
    baseline|compare|matrix)
      MODE="$1"
      shift
      ;;
    --rhel)
      RHEL="${2:?--rhel requires a value}"
      shift 2
      ;;
    --hardware)
      HARDWARE="${2:?--hardware requires a value}"
      shift 2
      ;;
    --label)
      LABEL="${2:?--label requires a value}"
      shift 2
      ;;
    --vu)
      VU="${2:?--vu requires a value}"
      shift 2
      ;;
    --vus)
      VUS="${2:?--vus requires a value}"
      shift 2
      ;;
    --repeats)
      REPEATS="${2:?--repeats requires a value}"
      shift 2
      ;;
    --skip-os-setup)
      SKIP_OS_SETUP=true
      shift
      ;;
    --skip-setup)
      SKIP_SETUP=true
      shift
      ;;
    --schedule)
      DO_SCHEDULE=true
      shift
      ;;
    --workload-name)
      WORKLOAD_NAME="${2:?--workload-name requires a value}"
      shift 2
      ;;
    --scalelab-cleanup)
      DO_SCALELAB_CLEANUP=true
      shift
      ;;
    --wait-timeout)
      WAIT_TIMEOUT="${2:?--wait-timeout requires a value}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      EXTRA+=("$@")
      break
      ;;
    *)
      EXTRA+=("$1")
      shift
      ;;
  esac
done

if [[ -z "$MODE" ]]; then
  usage >&2
  exit 1
fi

if [[ -z "$RHEL" ]]; then
  echo "error: --rhel VERSION is required (e.g. --rhel 9.0)" >&2
  exit 1
fi

RHEL_RELEASE_ROOT="$(rhel_release_root "$RHEL")"

if [[ "$MODE" == matrix ]]; then
  MODE=compare
fi

# ── Phase 0: ScaleLab scheduling (optional) ────────────────────────────

run_schedule

# Release hosts on exit if --scalelab-cleanup was requested (even on failure).
if [[ "$DO_SCALELAB_CLEANUP" == "true" ]]; then
  trap 'run_scalelab_cleanup' EXIT
fi

# Rebuild ANSIBLE_ARGS now that inventory.local.ini may have been created.
ANSIBLE_ARGS=(-i inventory.ini)
if [[ -f "$INVENTORY_LOCAL" ]]; then
  ANSIBLE_ARGS+=(-i "$INVENTORY_LOCAL")
fi
ANSIBLE_ARGS+=(-e "bench_rhel_release_id=${RHEL}")
[[ -n "$HARDWARE" ]] && ANSIBLE_ARGS+=(-e "cpt_hardware_profile=${HARDWARE}")
[[ -n "$LABEL" ]] && ANSIBLE_ARGS+=(-e "cpt_profile_label=${LABEL}")
if [[ "$MODE" == baseline ]]; then
  ANSIBLE_ARGS+=(-e cpt_establish_baseline=true)
fi

# ── Phase 1 & 2: OS setup + provisioning (run once) ────────────────────

run_os_setup
run_setup

# ── Phase 3 & 4: Benchmark + cleanup ───────────────────────────────────

if [[ -n "$VU" ]]; then
  ANSIBLE_ARGS+=(-e "hammerdb_virtual_users=${VU}")
  _cleanup_suffix=""
  [[ "$CPT_CLEANUP" == "true" ]] && _cleanup_suffix=", then cleanup.yaml"
  echo "==> DB-CPT-RHEL ${MODE} run (single VU=${VU}, site.yml${_cleanup_suffix})"
  echo "    RHEL bench: ${RHEL} (${RHEL_RELEASE_ROOT})"
  [[ -n "$HARDWARE" ]] && echo "    hardware:   ${HARDWARE}"
  [[ -n "$LABEL" ]] && echo "    label:      ${LABEL}"
  echo

  ansible-playbook playbooks/site.yml "${ANSIBLE_ARGS[@]}" "${EXTRA[@]}"
  if [[ "$CPT_CLEANUP" == "true" ]]; then
    run_cleanup
  fi
  exit 0
fi

run_vu_sweep "${MODE} run (site.yml per VU, cleanup after each)"
