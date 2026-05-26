# setup_EL9.yaml — RHEL 9.0 Setup with PGDG Workaround

This playbook is identical to [`setup.yaml`](setup.md) **except** it includes
a workaround for installing PGDG PostgreSQL packages on RHEL 9.0 (and other
early 9.x minors before 9.7).

For the full explanation of every provisioning step (storage, kernel tuning,
PostgreSQL config, HammerDB, PCP, connectivity checks), see
[setup.md](setup.md). This document only covers the RHEL 9.0-specific
workaround.

## When to use this playbook

```bash
# Use setup_EL9.yaml when bench runs RHEL 9.0 (or any 9.x < 9.7):
ansible-playbook playbooks/setup_EL9.yaml

# Use setup.yaml for RHEL 10.x, 9.7+, or any latest release:
ansible-playbook playbooks/setup.yaml
```

## The problem

PGDG (PostgreSQL Global Development Group) builds its RPM packages against the
**latest** RHEL 9.x libraries. When those packages are installed on RHEL 9.0,
three shared-library dependencies are too old to satisfy the PGDG RPMs:

| Library | RHEL 9.0 ships | PGDG needs |
|---------|---------------|------------|
| `openldap` | 2.6.2 (9.0 GA) | Symbols from 2.6.6+ (9.7+) |
| `openssl-libs` | 3.0.1 (9.0 GA) | Symbols/SONAMEs from 3.0.7+ (9.7+) |
| `openssh-server` / `openssh-clients` | 8.7p1-7 (9.0 GA) | Linked against the newer OpenSSL |

Without the workaround, `dnf install postgresql17-server` fails with unresolved
dependency errors.

A second issue is that RHEL 9.0 resolves the DNF variable `$releasever` to
`9.0` (the full version string) instead of `9` (the major). PGDG repo URLs use
`/rhel-9-x86_64/`, so `$releasever = 9.0` produces a 404.

## What the workaround does

The workaround runs **before** the PGDG repo RPM is installed and consists of
these steps:

### 1. Fix stale `$releasever` from prior partial runs

```yaml
- name: Fix stale PGDG repo releasever from prior partial runs
  command: sed -i 's|/rhel-$releasever-|/rhel-{{ _pgdg_el_major }}-|g'
           /etc/yum.repos.d/pgdg-redhat-all.repo
```

If a previous setup attempt left behind a PGDG repo file with the broken
`$releasever` token, this replaces it with the correct EL major (`9`) so
subsequent `dnf` commands can reach the PGDG mirrors.

### 2. Check whether the dependency repo is needed

```yaml
- name: Check if PGDG dependency repo is needed
  set_fact:
    _pgdg_needs_deps_repo: >-
      {{ ansible_distribution_version is version((_pgdg_el_major ~ '.7'), '<') }}
```

Compares the host's actual RHEL version against `<major>.7`. If the host is
below 9.7, the flag is set to `true` and all subsequent workaround tasks run.

### 3. Fail-safe for missing `pgdg_deps_baseurl`

If the dependency repo is needed but `pgdg_deps_baseurl` is not set in
inventory, the playbook fails immediately with a clear message telling the
operator which variable to set and what URL format to use.

### 4. Add a temporary yum repo for newer libraries

```yaml
- name: Add temporary repo for PGDG library dependencies
  yum_repository:
    name: pgdg-deps
    baseurl: "{{ pgdg_deps_baseurl }}"
    gpgcheck: false
    enabled: false
```

Creates a disabled repo (`pgdg-deps`) pointing at a RHEL 9.8+ compose BaseOS.
The repo is kept disabled so normal `dnf` operations do not pull from it
accidentally.

### 5. Update the three dependency packages

```yaml
- name: Update PGDG dependency libraries and openssh from newer compose
  dnf:
    name: [openldap, openssl-libs, openssh-server, openssh-clients]
    state: latest
    allowerasing: true
    disablerepo: "*"
    enablerepo: pgdg-deps
```

Upgrades only `openldap`, `openssl-libs`, `openssh-server`, and
`openssh-clients` from the temporary repo. `disablerepo: "*"` +
`enablerepo: pgdg-deps` ensures nothing else is touched. `allowerasing: true`
allows DNF to replace the older packages.

### 6. Restart sshd and wait

Because `openssl-libs` and `openssh-*` were upgraded, the running `sshd` must
be restarted to load the new shared libraries. The playbook then waits up to
30 seconds for port 22 to come back before continuing (Ansible's SSH connection
would break otherwise).

### 7. Remove the temporary repo

The `pgdg-deps` repo is removed so it does not interfere with future system
updates or `os-setup.yaml` runs that pin the host to RHEL 9.0.

### 8. Install PGDG repo RPM with pinned `releasever`

```yaml
- name: Install the PGDG repository RPM
  dnf:
    name: "{{ _pgdg_repo_rpm }}"
    releasever: "{{ _pgdg_el_major }}"
```

The `releasever` parameter forces DNF to use `9` instead of the system's
`9.0`, so the PGDG RPM's `%post` scriptlets write correct repo URLs.

### 9. Fix `$releasever` in the installed repo files

```yaml
- name: Fix PGDG repo files to use EL major version instead of pinned releasever
  command: sed -i 's|/rhel-$releasever-|/rhel-{{ _pgdg_el_major }}-|g'
           /etc/yum.repos.d/pgdg-redhat-all.repo
```

Even with the pinned `releasever` during install, the repo file templates still
embed `$releasever`. This sed replaces every occurrence with the literal major
version so future `dnf` operations resolve correctly regardless of the
system's `$releasever`.

## Required inventory variable

Set this in `[bench:vars]` in `inventory.ini`:

```ini
pgdg_deps_baseurl=https://download.eng.pnq.redhat.com/rhel-9/rel-eng/RHEL-9/latest-RHEL-9.8.0/compose/BaseOS/x86_64/os/
```

Point it at any RHEL 9.7+ or 9.8+ compose BaseOS URL. The playbook only pulls
the four library packages listed above from this repo.
