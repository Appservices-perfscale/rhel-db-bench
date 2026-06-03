#!/usr/bin/env python3
"""
Release watcher for DB-CPT automation.

Auto-discovers RHEL GA releases by scanning compose mirror directory listings.
No hardcoded version lists — the watcher starts from a configured major
(e.g. 9), increments until the mirror returns 404, and parses GA symlinks:
  RHEL 9 style:  latest-RHEL-9.0.0/
  RHEL 10+ style: latest-RHEL-10.0/, latest-RHEL-10.2/
Pre-release symlinks (RC, Beta, Alpha) are skipped.

Each discovered release is compared against state/last-tested.json.
The first untested release triggers exit code 2 and writes
inventory/generated/rhel-ga.ini (bench-only overlay).

Exit codes:
    0 — no new release found (all discovered releases already tested)
    2 — new release detected; overlay written

Usage:
    python3 scripts/release-watcher.py --print
    python3 scripts/release-watcher.py --print --dry-run
"""

import argparse
import json
import re
import sys
import urllib.request
import urllib.error
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional


def load_yaml_simple(path: Path) -> dict:
    """Minimal YAML-subset loader (avoids PyYAML dependency on controller).
    Handles flat keys, simple lists, and nested single-level mappings."""
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f)
    except ImportError:
        pass
    data: dict = {}
    current_key = None
    current_list: Optional[list] = None
    current_dict: Optional[dict] = None
    with open(path) as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(stripped)
            if indent == 0 and ":" in stripped:
                if current_list is not None and current_key:
                    data[current_key] = current_list
                    current_list = None
                if current_dict is not None and current_key:
                    data[current_key] = current_dict
                    current_dict = None
                k, _, v = stripped.partition(":")
                k = k.strip().strip('"').strip("'")
                v = v.strip().strip('"').strip("'")
                if v:
                    data[k] = v
                else:
                    current_key = k
                continue
            if stripped.startswith("- ") and current_key and indent > 0:
                if current_list is None:
                    current_list = []
                val = stripped[2:].strip().strip('"').strip("'")
                current_list.append(val)
                continue
            if ":" in stripped and indent > 0 and current_key:
                if current_dict is None:
                    current_dict = {}
                k2, _, v2 = stripped.partition(":")
                k2 = k2.strip().strip('"').strip("'")
                v2 = v2.strip().strip('"').strip("'")
                current_dict[k2] = v2
                continue
    if current_list is not None and current_key:
        data[current_key] = current_list
    if current_dict is not None and current_key:
        data[current_key] = current_dict
    return data


# ── Mirror directory scanner ────────────────────────────────────────────


class _LinkParser(HTMLParser):
    """Extract href values from an HTML directory listing."""
    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for name, val in attrs:
                if name == "href" and val:
                    self.links.append(val)


def fetch_directory_links(url: str, timeout: int = 15) -> list[str]:
    """GET the directory listing page and return all href values."""
    req = urllib.request.Request(url)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError):
        return []
    parser = _LinkParser()
    parser.feed(html)
    return parser.links


_PRE_GA_MARKERS = ("-RC-", "-Beta-", "-Alpha-")


def _is_ga_symlink(link: str) -> bool:
    return not any(marker in link for marker in _PRE_GA_MARKERS)


def discover_minor_tags(dir_url: str) -> list[str]:
    """Scan a compose mirror directory and return sorted GA symlink tags.

    Supports both naming schemes on the internal compose mirror:
      latest-RHEL-9.0.0/   (RHEL 9 three-part minors)
      latest-RHEL-10.0/    (RHEL 10+ two-part minors)

    When both latest-RHEL-9.0/ and latest-RHEL-9.0.0/ exist, prefers the
    three-part form (canonical GA symlink for RHEL 9).
    """
    links = fetch_directory_links(dir_url)
    three_part = re.compile(r"latest-RHEL-(\d+\.\d+\.\d+)/?$")
    two_part = re.compile(r"latest-RHEL-(\d+\.\d+)/?$")

    tags: list[str] = []
    release_ids_seen: set[str] = set()

    for link in links:
        if not _is_ga_symlink(link):
            continue
        m = three_part.search(link)
        if m:
            tag = m.group(1)
            if tag not in tags:
                tags.append(tag)
                release_ids_seen.add(parse_release_id(tag))
            continue
        m = two_part.search(link)
        if m:
            tag = m.group(1)
            rid = parse_release_id(tag)
            if rid in release_ids_seen or tag in tags:
                continue
            tags.append(tag)
            release_ids_seen.add(rid)

    def version_key(tag: str):
        return tuple(int(p) for p in tag.split("."))

    return sorted(tags, key=version_key)


def discover_all_targets(dir_template: str, scan_from: int,
                         max_gap: int = 2) -> list[str]:
    """Scan majors starting from scan_from.  Keeps going through up to
    max_gap consecutive missing majors before stopping."""
    all_tags: list[str] = []
    consecutive_misses = 0
    major = scan_from
    while consecutive_misses <= max_gap:
        dir_url = dir_template.replace("{major}", str(major))
        tags = discover_minor_tags(dir_url)
        if tags:
            all_tags.extend(tags)
            consecutive_misses = 0
        else:
            consecutive_misses += 1
        major += 1
    return all_tags


# ── Compose resolution ──────────────────────────────────────────────────


def resolve_compose(url: str, timeout: int = 15) -> Optional[str]:
    """HEAD-follow the compose URL and extract the resolved build ID."""
    repomd_url = f"{url}/compose/BaseOS/x86_64/os/repodata/repomd.xml"
    req = urllib.request.Request(repomd_url, method="HEAD")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        final_url = resp.url
        last_modified = resp.headers.get("Last-Modified", "")
        m = re.search(r"RHEL-([\d.]+-[\w.]+)", final_url)
        if m:
            return m.group(1)
        if last_modified:
            return last_modified
        return final_url
    except (urllib.error.URLError, OSError):
        return None


# ── Helpers ─────────────────────────────────────────────────────────────


def parse_major(minor_tag: str) -> int:
    """'9.7.0' -> 9"""
    return int(minor_tag.split(".")[0])


def parse_release_id(symlink_tag: str) -> str:
    """Map mirror symlink tag to inventory release id.

    '9.7.0' -> '9.7'   (RHEL 9 three-part symlink)
    '10.0'  -> '10.0'  (RHEL 10+ two-part symlink)
    """
    parts = symlink_tag.split(".")
    if len(parts) >= 3:
        return f"{parts[0]}.{parts[1]}"
    return symlink_tag


def build_compose_root(template: str, minor_tag: str) -> str:
    major = str(parse_major(minor_tag))
    return template.replace("{major}", major).replace("{minor_tag}", minor_tag)


def pick_ostype_for_major(os_list_url: str, major: int,
                          timeout: int = 10) -> Optional[str]:
    """Query QUADS os_list and find the best RHEL title for the given major."""
    try:
        req = urllib.request.Request(os_list_url)
        resp = urllib.request.urlopen(req, timeout=timeout)
        entries = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None
    prefix = f"RHEL {major}."
    candidates = [e["Title"] for e in entries
                  if e.get("Title", "").startswith(prefix)]
    if not candidates:
        return None

    def version_key(title: str):
        nums = title.replace("RHEL ", "").split(".")
        return tuple(int(n) for n in nums if n.isdigit())

    return sorted(candidates, key=version_key)[-1]


# ── State ───────────────────────────────────────────────────────────────


def load_state(state_path: Path) -> dict:
    if state_path.exists():
        return json.loads(state_path.read_text())
    return {"by_target": {}, "last_host_major": None}


# ── Inventory overlay ──────────────────────────────────────────────────


def write_bench_inventory(output_path: Path, compose_root: str,
                          release_id: str):
    """Write generated inventory overlay for bench only."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        f"# Auto-generated by scripts/release-watcher.py — do not edit.\n"
        f"# Bench overlay for RHEL {release_id}."
        f" Client stays at inventory.ini [client:vars].\n"
        f"[bench:vars]\n"
        f"os_prep_rhel_release_root={compose_root}\n"
        f"os_prep_rhel_release_id={release_id}\n"
    )
    output_path.write_text(content)


# ── Main ────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="CPT release watcher")
    parser.add_argument("--config", default="config/cpt-automation.yaml",
                        help="Path to automation config")
    parser.add_argument("--project-root", default=".",
                        help="Project root directory")
    parser.add_argument("--print", dest="print_json", action="store_true",
                        help="Print release info as JSON to stdout")
    parser.add_argument("--dry-run", action="store_true",
                        help="Check only — do not write inventory overlay")
    args = parser.parse_args()

    root = Path(args.project_root)
    cfg = load_yaml_simple(root / args.config)

    mirror = cfg.get("compose_mirror", {})
    dir_template = mirror.get("dir_url_template", "")
    compose_template = mirror.get("compose_url_template", "")
    scan_from = int(mirror.get("scan_from_major", 9))

    state_file = root / cfg.get("state_file", "state/last-tested.json")
    gen_inv = root / cfg.get("generated_inventory",
                             "inventory/generated/rhel-ga.ini")
    os_list_url = cfg.get("foreman", {}).get("os_list_url", "")

    state = load_state(state_file)
    by_target = state.get("by_target", {})

    # ── Auto-discover all available targets from the mirror ──
    targets = discover_all_targets(dir_template, scan_from)

    if args.print_json and not targets:
        print(json.dumps({"changed": False,
                          "info": "no targets discovered from mirror"}))
        sys.exit(0)

    new_release = None

    for target in targets:
        compose_root = build_compose_root(compose_template, target)
        build_id = resolve_compose(compose_root)
        if build_id is None:
            continue

        prev = by_target.get(target, {}).get("compose_build")
        if prev == build_id:
            continue

        release_id = parse_release_id(target)
        major = parse_major(target)
        ostype = (pick_ostype_for_major(os_list_url, major)
                  if os_list_url else None)

        last_host_major = state.get("last_host_major")
        needs_wipe = (last_host_major is not None
                      and last_host_major != major)

        new_release = {
            "target": target,
            "release_id": release_id,
            "major": major,
            "compose_root": compose_root,
            "compose_build": build_id,
            "ostype": ostype,
            "needs_wipe": needs_wipe,
        }
        break  # process one new release at a time

    if new_release is None:
        if args.print_json:
            discovered = [parse_release_id(t) for t in targets]
            print(json.dumps({"changed": False,
                              "discovered": discovered}))
        sys.exit(0)

    if not args.dry_run:
        write_bench_inventory(gen_inv, new_release["compose_root"],
                              new_release["release_id"])

    if args.print_json:
        discovered = [parse_release_id(t) for t in targets]
        print(json.dumps({"changed": True, "discovered": discovered,
                          **new_release}, indent=2))

    sys.exit(2)


if __name__ == "__main__":
    main()
