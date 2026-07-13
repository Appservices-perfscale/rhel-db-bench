# DB-CPT-RHEL Ansible controller image.
# Orchestrates benchmark playbooks against remote bench/client bare-metal hosts.
#
# Build:
#   podman build -t quay.io/rhcloudperfscale/db-cpt-rhel:latest .
# Push (after podman login quay.io):
#   podman push quay.io/rhcloudperfscale/db-cpt-rhel:latest
#
# Run (mount secrets/config at runtime; do not bake into the image):
#   podman run --rm -v ./inventory.local.ini:/opt/db-cpt-rhel/inventory.local.ini:Z \
#     -e PGPASSWORD -v ./pass_or_fail_cfg.yaml:/opt/db-cpt-rhel/pass_or_fail_cfg.yaml:Z \
#     quay.io/rhcloudperfscale/db-cpt-rhel:latest \
#     compare --rhel 9.4 --hardware r650

FROM registry.access.redhat.com/ubi9/ubi:latest

LABEL org.opencontainers.image.title="DB-CPT-RHEL" \
      org.opencontainers.image.description="Ansible controller for PostgreSQL TPC-C competitive performance testing on RHEL" \
      org.opencontainers.image.source="https://github.com/Appservices-perfscale/rhel-db-bench"

ENV DB_CPT_RHEL_ROOT=/opt/db-cpt-rhel \
    ANSIBLE_CONFIG=/opt/db-cpt-rhel/ansible.cfg \
  PATH="/opt/db-cpt-rhel/scripts:${PATH}"

RUN INSTALL_PKGS="python3-pip python3-jinja2 git-core openssh-clients jq" && \
    dnf -y --setopt=install_weak_deps=0 install $INSTALL_PKGS && \
    dnf -y clean all && \
    python3 -m pip install --no-cache-dir 'ansible-core>=2.14'

WORKDIR ${DB_CPT_RHEL_ROOT}

# Dependency manifests first for better layer caching.
COPY requirements.yml requirements.txt ansible.cfg inventory.ini inventory.local.ini.example quads_cfg.yaml.example ./
RUN ansible-galaxy collection install -r requirements.yml -p ./collections

COPY playbooks/ playbooks/
COPY templates/ templates/
COPY group_vars/ group_vars/
COPY scripts/ scripts/

RUN python3 -m pip install --no-cache-dir -r requirements.txt && \
    python3 -m pip install --no-cache-dir --no-deps \
      'git+https://github.com/redhat-performance/opl.git#subdirectory=extras' && \
    chmod +x scripts/container-sanity-check.sh scripts/cpt-run.sh && \
    scripts/container-sanity-check.sh

WORKDIR ${DB_CPT_RHEL_ROOT}
ENTRYPOINT ["./scripts/cpt-run.sh"]
