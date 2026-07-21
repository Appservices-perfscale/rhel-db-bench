# Container image

The controller image has Ansible, Galaxy collections, Python bits (pass/fail +
DB upload), and the playbooks. It does **not** run PostgreSQL or HammerDB —
those stay on the bare-metal bench and client.

## Registries

| Registry | Image |
|----------|-------|
| Public (manual publish) | `quay.io/rhcloudperfscale/db-cpt-rhel` |
| Konflux workspace | `quay.io/redhat-user-workloads/hcc-perfscale-tenant/db-cpt-rhel` |

Create `rhcloudperfscale/db-cpt-rhel` on [quay.io](https://quay.io/organization/rhcloudperfscale)
before the first push if it does not exist yet.

## Build locally

```bash
podman build -t quay.io/rhcloudperfscale/db-cpt-rhel:latest .
```

The build runs `scripts/container-sanity-check.sh` (tools, OPL, Galaxy
collections, playbook syntax).

Without a container, install the same Python deps with
`./scripts/install-controller-deps.sh`.

## Push to Quay

```bash
podman login quay.io
podman push quay.io/rhcloudperfscale/db-cpt-rhel:latest

TAG="$(git rev-parse --short HEAD)"
podman tag quay.io/rhcloudperfscale/db-cpt-rhel:latest \
  quay.io/rhcloudperfscale/db-cpt-rhel:${TAG}
podman push quay.io/rhcloudperfscale/db-cpt-rhel:${TAG}
```

## Run a benchmark from the image

Mount secrets at runtime — do not bake them into the image:

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

The image entrypoint is `scripts/cpt-run.sh`, so arguments are the same as a
local `./scripts/cpt-run.sh …` run. For auto-schedule, also mount
`quads_cfg.yaml` and pass `--schedule --workload-name '…'`.

---

## Jenkins (HCEPERF-1487)

Jenkins wiring is still being onboarded. The expected layout (when added) is a
`Jenkinsfile.groovy` in this repo plus a JOBDSL job in
[ci-configs](https://gitlab.cee.redhat.com/redhat-performance/ci-configs)
(`src/jobs/DbCptRhelJob.groovy`), using the usual compute + jnlp pod and a
shared PVC.

Until those files land, treat this section as the **credential and parameter
contract** the job should use.

### Credentials to create

| Credential ID | Type | Contents |
|---------------|------|----------|
| `db-cpt-rhel-inventory` | Secret file | `inventory.local.ini` (hosts + SSH; Foreman/Badfish if you rebuild majors) |
| `db-cpt-rhel-pass-or-fail` | Secret file | `pass_or_fail_cfg.yaml` |
| `db-cpt-rhel-archive-cfg` | Secret file | `archive_cfg.yaml` (prefer no password in-file; use env below) |
| `db-cpt-rhel-pgpassword` | Secret text | PostgreSQL password → `PGPASSWORD` |
| `db-cpt-rhel-quads-cfg` | Secret file *(optional)* | `quads_cfg.yaml` — only if the job runs `--schedule` / `--scalelab-cleanup` |

Also set `CPT_ARTIFACT_ROOT` on the job (e.g. `/workspace/ARTIFACTS/DB-CPT-RHEL`)
so run logs land on the PVC. That is an env var, not a secret.

### Expected job parameters

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `MODE` | `compare` | `baseline`, `compare`, or `matrix` |
| `RHEL_VERSION` | `9.4` | Bench RHEL for os-setup + benchmark |
| `HARDWARE` | `r650` | Hardware cohort tag |
| `VUS` | *(empty)* | Override VU list; empty = inventory matrix |
| `SKIP_OS_SETUP` | `false` | Skip os-setup when hosts are already pinned |
| `SKIP_SETUP` | `false` | Skip provisioning when hosts are already set up |
| `CONTAINER_IMAGE` | `quay.io/rhcloudperfscale/db-cpt-rhel:latest` | Controller image |

---

## Konflux (HCEPERF-1517)

| Resource | URL |
|----------|-----|
| Tenant UI | [hcc-perfscale-tenant applications](https://konflux-ui.apps.stone-prd-rh01.pg1f.p1.openshiftapps.com/ns/hcc-perfscale-tenant/applications) |
| Getting started | [Konflux components & applications](https://konflux.pages.redhat.com/docs/users/getting-started/components-applications.html) |
| Tenant GitOps | [hcc-perfscale-tenant config](https://gitlab.cee.redhat.com/releng/konflux-release-data/-/tree/main/tenants-config/cluster/stone-prd-rh01/tenants/hcc-perfscale-tenant) |

Pipelines in `.tekton/`:

- `db-cpt-rhel-push.yaml` — build on push to `main`
- `db-cpt-rhel-pull-request.yaml` — PR builds (image expires after ~5 days)

Onboard GitHub `Appservices-perfscale/rhel-db-bench` as app/component
`db-cpt-rhel` in `hcc-perfscale-tenant`. Konflux builds land in the workspace
Quay path above; promote to `quay.io/rhcloudperfscale/db-cpt-rhel` with a
release pipeline when ready.

[MintMaker](https://konflux.pages.redhat.com/docs/users/mintmaker/user.html)
reads `renovate.json` for dependency PRs.
