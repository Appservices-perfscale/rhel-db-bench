# Container image

The DB-CPT-RHEL **controller** image bundles Ansible, Galaxy collections, Python
dependencies (OPL pass/fail, DB upload), and all playbooks/scripts needed to
orchestrate benchmarks on remote bench/client hosts. It does **not** run
PostgreSQL or HammerDB — those stay on the bare-metal targets.

## Image registry

| Registry | Image |
|----------|-------|
| Public (manual publish) | `quay.io/rhcloudperfscale/db-cpt-rhel` |
| Konflux build workspace | `quay.io/redhat-user-workloads/hcc-perfscale-tenant/db-cpt-rhel` |

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

On Jenkins, set `CPT_ARTIFACT_ROOT` and mount it similarly to the loadtest probe
job pattern (container provides the toolchain; the job supplies inventory,
credentials, and artifact paths).

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
Konflux publishes to `quay.io/redhat-user-workloads/hcc-perfscale-tenant/db-cpt-rhel`.
Configure a release pipeline to promote images to `quay.io/rhcloudperfscale/db-cpt-rhel`.

[MintMaker](https://konflux.pages.redhat.com/docs/users/mintmaker/user.html) reads
`renovate.json` in this repo for dependency update PRs. Enable auto-merge in the
tenant or GitHub org settings once Konflux build checks are required on PRs.
