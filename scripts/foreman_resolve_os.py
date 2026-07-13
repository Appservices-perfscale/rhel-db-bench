#!/usr/bin/env python3
"""
Resolve the best Foreman operating system for a target RHEL release.

Foreman may only offer RHEL 10.0 while the benchmark targets 10.2. This script
picks the highest RHEL {major}.x title on Foreman that is still at or below the
target minor (e.g. 10.0 for target 10.2), so os-setup can Foreman-install that
build and distro-sync the rest of the way.

Usage:
    python3 scripts/foreman_resolve_os.py \\
        --url https://foreman.rdu2.scalelab.redhat.com/api/v2 \\
        --user cloud04 --password secret \\
        --release 10.2

Prints JSON to stdout for Ansible.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from foreman_rebuild import Foreman  # noqa: E402

RHEL_TITLE_RE = re.compile(r"^RHEL (\d+)\.(\d+)$")


def parse_release_id(release_id: str) -> tuple[int, int]:
    text = str(release_id).strip()
    major, minor = text.split(".", 1)
    return int(major), int(minor)


def parse_rhel_title(title: str) -> tuple[int, int] | None:
    match = RHEL_TITLE_RE.match((title or "").strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def resolve_rhel_release(operatingsystems: list[dict], target_release_id: str) -> dict:
    target_major, target_minor = parse_release_id(target_release_id)
    candidates: list[dict] = []

    for entry in operatingsystems:
        title = (entry.get("title") or entry.get("name") or "").strip()
        parsed = parse_rhel_title(title)
        if not parsed or parsed[0] != target_major:
            continue
        major, minor = parsed
        candidates.append(
            {
                "title": title,
                "release_id": f"{major}.{minor}",
                "minor": minor,
            }
        )

    if not candidates:
        available = sorted(
            {
                (entry.get("title") or entry.get("name") or "").strip()
                for entry in operatingsystems
                if (entry.get("title") or entry.get("name"))
            }
        )
        raise LookupError(
            f"No Foreman OS for RHEL {target_major}.x (target {target_release_id}). "
            f"Available: {available}"
        )

    eligible = [candidate for candidate in candidates if candidate["minor"] <= target_minor]
    if not eligible:
        available_ids = sorted(candidate["release_id"] for candidate in candidates)
        raise LookupError(
            f"No Foreman RHEL {target_major}.x at or below {target_release_id}. "
            f"Available for major {target_major}: {available_ids}"
        )

    best = max(eligible, key=lambda candidate: candidate["minor"])
    foreman_release_id = best["release_id"]
    return {
        "target_release_id": str(target_release_id),
        "foreman_os_title": best["title"],
        "foreman_release_id": foreman_release_id,
        "distro_sync_needed": foreman_release_id != str(target_release_id),
        "available_rhel_releases": sorted({candidate["release_id"] for candidate in candidates}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Foreman API v2 base URL")
    parser.add_argument("--user", required=True, help="Foreman username")
    parser.add_argument("--password", required=True, help="Foreman password")
    parser.add_argument(
        "--release",
        required=True,
        help="Target RHEL release id (e.g. 10.2)",
    )
    args = parser.parse_args()

    foreman = Foreman(args.url, args.user, args.password)
    try:
        result = foreman._get("/operatingsystems", params={"per_page": 250})
        resolved = resolve_rhel_release(result.get("results", []), args.release)
    except LookupError as exc:
        json.dump({"error": str(exc)}, sys.stdout)
        return 1

    json.dump(resolved, sys.stdout, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
