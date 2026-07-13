#!/usr/bin/env python3
"""
Foreman host rebuild — set OS, media, partition table, and trigger PXE reinstall.

Adapted from the QUADS project Foreman client:
https://github.com/quadsproject/quads/blob/latest/src/quads/tools/external/foreman.py

Usage (standalone):
    python3 scripts/foreman_rebuild.py \
        --url https://foreman.scalelab.redhat.com/api/v2 \
        --user admin --password secret \
        --host f20-h01-000-r650.rdu2.scalelab.redhat.com \
        --os "RHEL 10.0"

Called by Ansible via playbooks/tasks/os-prep-foreman-rebuild.yaml.
Prints JSON to stdout for Ansible's command module to capture.
"""

import argparse
import json
import sys
import urllib3

import requests
from requests.auth import HTTPBasicAuth

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TIMEOUT = 120


class Foreman:
    def __init__(self, url, username, password):
        self.url = url.rstrip("/")
        self.auth = HTTPBasicAuth(username, password)

    def _get(self, endpoint, params=None):
        r = requests.get(
            self.url + endpoint,
            auth=self.auth,
            verify=False,
            timeout=TIMEOUT,
            params=params,
        )
        r.raise_for_status()
        return r.json()

    def _put(self, endpoint, data):
        r = requests.put(
            self.url + endpoint,
            json=data,
            auth=self.auth,
            verify=False,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()

    def get_host_id(self, hostname):
        result = self._get("/hosts", params={"search": f"name={hostname}"})
        for host in result.get("results", []):
            if host["name"] == hostname:
                return host["id"]
        raise LookupError(f"Host '{hostname}' not found in Foreman")

    def get_os_id(self, os_title):
        result = self._get("/operatingsystems", params={"per_page": 250})
        for os_entry in result.get("results", []):
            if os_entry.get("title") == os_title:
                return os_entry["id"]
        available = [o.get("title", o.get("name")) for o in result.get("results", [])]
        raise LookupError(
            f"OS '{os_title}' not found in Foreman. Available: {available}"
        )

    def get_mediums(self, os_id):
        result = self._get(f"/operatingsystems/{os_id}/media")
        return result.get("results", [])

    def get_ptables(self, os_id):
        result = self._get(f"/operatingsystems/{os_id}/ptables")
        return result.get("results", [])

    def set_host_parameter(self, host_id, name, value):
        params_result = self._get(
            f"/hosts/{host_id}/parameters",
            params={"search": f"name={name}"},
        )
        existing = [
            p for p in params_result.get("results", []) if p["name"] == name
        ]
        if existing:
            self._put(
                f"/hosts/{host_id}/parameters/{existing[0]['id']}",
                {"parameter": {"value": value}},
            )
        else:
            r = requests.post(
                self.url + f"/hosts/{host_id}/parameters",
                json={"parameter": {"name": name, "value": value}},
                auth=self.auth,
                verify=False,
                timeout=TIMEOUT,
            )
            r.raise_for_status()

    def put_host(self, host_id, data):
        self._put(f"/hosts/{host_id}", {"host": data})

    def rebuild(self, hostname, os_title):
        host_id = self.get_host_id(hostname)
        os_id = self.get_os_id(os_title)

        mediums = self.get_mediums(os_id)
        if not mediums:
            raise LookupError(f"No install media configured for OS id={os_id}")
        medium = mediums[0]

        ptables = self.get_ptables(os_id)
        if not ptables:
            raise LookupError(f"No partition tables configured for OS id={os_id}")
        ptable = ptables[0]

        self.set_host_parameter(host_id, "overcloud", "true")

        self.put_host(host_id, {
            "operatingsystem_id": os_id,
            "medium_id": medium["id"],
            "ptable_id": ptable["id"],
            "build": True,
        })

        return {
            "host": hostname,
            "host_id": host_id,
            "os_title": os_title,
            "os_id": os_id,
            "medium": medium["name"],
            "ptable": ptable["name"],
            "build": True,
        }


def main():
    parser = argparse.ArgumentParser(
        description="Trigger a Foreman host rebuild for a target OS."
    )
    parser.add_argument("--url", required=True, help="Foreman API v2 base URL")
    parser.add_argument("--user", required=True, help="Foreman username")
    parser.add_argument("--password", required=True, help="Foreman password")
    parser.add_argument("--host", required=True, help="FQDN of the host to rebuild")
    parser.add_argument("--os", required=True, help='Target OS title (e.g. "RHEL 10.0")')

    args = parser.parse_args()

    foreman = Foreman(args.url, args.user, args.password)
    try:
        result = foreman.rebuild(args.host, args.os)
    except (LookupError, requests.HTTPError) as exc:
        json.dump({"error": str(exc)}, sys.stdout)
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
