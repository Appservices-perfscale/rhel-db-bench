# Container image

The DB-CPT-RHEL **controller** image bundles Ansible, Galaxy collections, Python
dependencies (OPL pass/fail, DB upload), and all playbooks/scripts needed to
orchestrate benchmarks on remote bench/client hosts. It does **not** run
PostgreSQL or HammerDB — those stay on the bare-metal targets.

## Image registry

| Registry | Image |
|----------|-------|
| Public (manual publish) | `quay.io/rhcloudperfscale/db-cpt-rhel` |
| Konflux build workspace | `quay.io/rhcloudperfscale/db-cpt-rhel:on-pr-{{revision}}` |

Create the `rhcloudperfscale/db-cpt-rhel` repository on [quay.io](https://quay.io/organization/rhcloudperfscale) before the first push.

## Build locally

From the repository root:

```bash
podman build -t quay.io/rhcloudperfscale/db-cpt-rhel:latest .
```

The build runs `scripts/container-sanity-check.sh`, which verifies:

- `ansible`, `ansible-playbook`, `python3`, `jq`, `git`, `ssh`
- `psycopg2` and `pass_or_fail.py` (OPL core + extras)
- Ansible Galaxy collections from `requirements.yml`
- `ansible-playbook --syntax-check` on every playbook

For a non-container controller install, use the same Python deps via
`./scripts/install-controller-deps.sh`.

## Push to Quay

```bash
podman login quay.io
podman push quay.io/rhcloudperfscale/db-cpt-rhel:latest
```

Tag with a git SHA for traceability when publishing from CI or Konflux:

```bash
TAG="$(git rev-parse --short HEAD)"
podman tag quay.io/rhcloudperfscale/db-cpt-rhel:latest quay.io/rhcloudperfscale/db-cpt-rhel:${TAG}
podman push quay.io/rhcloudperfscale/db-cpt-rhel:${TAG}
```

## Run a benchmark

Mount runtime config and secrets — **do not bake them into the image**:

```bash
podman run --rm \
  -v "$PWD/inventory.local.ini:/opt/db-cpt-rhel/inventory.local.ini:Z" \
  -v "$PWD/pass_or_fail_cfg.yaml:/opt/db-cpt-rhel/pass_or_fail_cfg.yaml:Z" \
  -v "$PWD/archive_cfg.yaml:/opt/db-cpt-rhel/archive_cfg.yaml:Z" \
  -e PGPASSWORD \
  -v "$PWD/results:/opt/db-cpt-rhel/results:Z" \
  quay.io/rhcloudperfscale/db-cpt-rhel:latest \
  compare --rhel 9.4 --hardware r650
```

## Jenkins (HCEPERF-1487)

`Jenkinsfile.groovy` defines the pipeline and `jenkins/DbCptRhelJob.groovy`
provides the matching JOBDSL definition.  Both follow the same two-container
pod pattern used by the loadtest probe jobs (compute + jnlp sidecar on a
shared PVC).

### Required Jenkins credentials

| Credential ID | Type | Contents |
|---------------|------|----------|
| `db-cpt-rhel-inventory` | Secret file | `inventory.local.ini` (bench/client hosts + SSH passwords) |
| `db-cpt-rhel-pass-or-fail` | Secret file | `pass_or_fail_cfg.yaml` (regression DB config) |
| `db-cpt-rhel-archive-cfg` | Secret file | `archive_cfg.yaml` (result upload + artifact storage) |
| `db-cpt-rhel-pgpassword` | Secret text | `PGPASSWORD` for pass\_or\_fail and upload scripts |

### Job parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MODE` | `compare` | `baseline`, `compare`, or `matrix` |
| `RHEL_VERSION` | `9.4` | Bench RHEL for os-setup + benchmark |
| `HARDWARE` | `r650` | Hardware cohort tag |
| `VUS` | *(empty)* | Override VU list; empty = inventory matrix |
| `SKIP_OS_SETUP` | `false` | Skip os-setup when hosts are already pinned |
| `SKIP_SETUP` | `false` | Skip provisioning when hosts are already set up |
| `CONTAINER_IMAGE` | `quay.io/rhcloudperfscale/db-cpt-rhel:latest` | Controller image |

### Onboarding the job in ci-configs

Copy `jenkins/DbCptRhelJob.groovy` into the
[ci-configs](https://gitlab.cee.redhat.com/redhat-performance/ci-configs)
repository at `src/jobs/DbCptRhelJob.groovy`.  After the seed job runs, the
`DB-CPT-RHEL` pipeline appears in Jenkins and reads `Jenkinsfile.groovy` from
the `main` branch.

### Running

Trigger manually from the Jenkins UI with the desired parameters, or add a
`cron('...')` trigger in `Jenkinsfile.groovy` for nightly runs.  Benchmark
results are archived under `artifacts/` in each build.

## Konflux (HCEPERF-1517)

| Resource | URL |
|----------|-----|
| Konflux tenant UI | [hcc-perfscale-tenant applications](https://konflux-ui.apps.stone-prd-rh01.pg1f.p1.openshiftapps.com/ns/hcc-perfscale-tenant/applications) |
| Getting started | [Konflux components & applications](https://konflux.pages.redhat.com/docs/users/getting-started/components-applications.html) |
| Tenant GitOps | [hcc-perfscale-tenant config](https://gitlab.cee.redhat.com/releng/konflux-release-data/-/tree/main/tenants-config/cluster/stone-prd-rh01/tenants/hcc-perfscale-tenant) |

Pipeline definitions live in `.tekton/`:

- `db-cpt-rhel-push.yaml` — builds on push to `main`
- `db-cpt-rhel-pull-request.yaml` — builds on pull requests (image expires after 5 days)

Onboard the GitHub repo (`Appservices-perfscale/rhel-db-bench`) as application
`db-cpt-rhel` / component `db-cpt-rhel` in the `hcc-perfscale-tenant` namespace.
Konflux publishes to `quay.io/rhcloudperfscale/db-cpt-rhel`.
Configure a release pipeline to promote images to `quay.io/rhcloudperfscale/db-cpt-rhel`.

[MintMaker](https://konflux.pages.redhat.com/docs/users/mintmaker/user.html) reads
`renovate.json` in this repo for dependency update PRs. Enable auto-merge in the
tenant or GitHub org settings once Konflux build checks are required on PRs.
