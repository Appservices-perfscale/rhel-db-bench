#!/usr/bin/env python3
"""
Merge pmdumptext-format PCP sample logs into a benchmark report JSON.

Each log file is parsed independently:
  --log          -> merged under report["bench_metrics"]
  --client-log   -> merged under report["client_metrics"]

Parsed blocks are deep-merged with existing objects so Ansible metadata
(capture flags, metric names, intervals) is preserved alongside summary/samples.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

BYTES_PER_GIB = 1024**3
# pmdumptext rates for disk.* in this toolchain are treated as KB/s; rough 4 KiB IOP est.
KB_PER_4K_IOP = 4.0


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Nearest-rank percentile on a pre-sorted list."""
    if not sorted_vals:
        return 0.0
    k = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = int(math.floor(k))
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return round(sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo]), 2)


@dataclass
class MetricSummary:
    sample_count_valid_cpu: int = 0
    sample_count_valid_disk: int = 0
    sample_count_valid_mem: int = 0
    max_cpu_utilization_percent: float = 0.0
    avg_cpu_utilization_percent: float = 0.0
    p95_cpu_utilization_percent: float = 0.0
    max_cpu_user: float = 0.0
    avg_cpu_user: float = 0.0
    p95_cpu_user: float = 0.0
    max_cpu_sys: float = 0.0
    avg_cpu_sys: float = 0.0
    p95_cpu_sys: float = 0.0
    max_disk_read_kbps: float = 0.0
    avg_disk_read_kbps: float = 0.0
    p95_disk_read_kbps: float = 0.0
    max_disk_write_kbps: float = 0.0
    avg_disk_write_kbps: float = 0.0
    p95_disk_write_kbps: float = 0.0
    max_disk_total_kbps: float = 0.0
    avg_disk_total_kbps: float = 0.0
    p95_disk_total_kbps: float = 0.0
    max_read_iops_estimated: float = 0.0
    avg_read_iops_estimated: float = 0.0
    p95_read_iops_estimated: float = 0.0
    max_write_iops_estimated: float = 0.0
    avg_write_iops_estimated: float = 0.0
    p95_write_iops_estimated: float = 0.0
    max_iops_estimated: float = 0.0
    avg_iops_estimated: float = 0.0
    p95_iops_estimated: float = 0.0
    max_mem_used_gib: float = 0.0
    avg_mem_used_gib: float = 0.0
    p95_mem_used_gib: float = 0.0
    min_mem_free_gib: float = 0.0


def _deep_merge(base: Any, overlay: Any) -> Any:
    """Recursively merge mapping types; overlay wins for conflicts. Lists are replaced."""
    if isinstance(base, dict) and isinstance(overlay, dict):
        out = deepcopy(base)
        for key, val in overlay.items():
            if key in out and isinstance(out[key], dict) and isinstance(val, dict):
                out[key] = _deep_merge(out[key], val)
            else:
                out[key] = deepcopy(val)
        return out
    return deepcopy(overlay)


def _parse_scalar(cell: str) -> float | None:
    text = cell.strip()
    if not text or text == "?":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _infer_year_hint(path: Path) -> int:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).year
    except OSError:
        return datetime.now().year


def _parse_timestamp(ts_raw: str, year_hint: int) -> str | None:
    ts_raw = ts_raw.strip()
    if not ts_raw:
        return None
    for fmt in ("%a %b %d %H:%M:%S %Y", "%a %b %d %Y %H:%M:%S"):
        try:
            dt = datetime.strptime(ts_raw, fmt)
            return dt.isoformat(timespec="seconds")
        except ValueError:
            continue
    try:
        dt = datetime.strptime(
            f"{ts_raw} {year_hint}", "%a %b %d %H:%M:%S %Y"
        )
        return dt.isoformat(timespec="seconds")
    except ValueError:
        return None


def _cpu_util_percent(
    user: float | None, sys_v: float | None, idle: float | None
) -> float | None:
    """Match legacy reports: 100 * (user + sys) / (user + sys + idle) when idle is known."""
    if user is None or sys_v is None:
        return None
    if idle is not None:
        denom = user + sys_v + idle
        if denom <= 0:
            return None
        return round(100.0 * (user + sys_v) / denom, 2)
    return round(user + sys_v, 2)


def _kbps_to_iops_est(kbps: float | None) -> float | None:
    if kbps is None:
        return None
    return round(kbps / KB_PER_4K_IOP, 2)


# PCP reports byte rates; store kilobytes/s as *_kbps alongside disk metrics.
BYTES_PER_KB = 1024.0


def _parse_network_metric(name: str) -> tuple[str, str] | None:
    """If name is network.interface.{in,out}.bytes[iface], return (iface, 'in'|'out')."""
    m = re.match(
        r"network\.interface\.(in|out)\.bytes\[([^\]]+)\]",
        name.strip(),
    )
    if not m:
        return None
    direction, iface = m.group(1), m.group(2)
    return iface, direction


def _network_sample_key(iface: str, direction: str) -> str:
    safe = re.sub(r"[^\w\-]+", "_", iface.strip()).strip("_") or "if"
    return f"network_interface_{safe}_{direction}_kbps"


def _parse_disk_dev_metric(name: str) -> tuple[str, str] | None:
    """If name is disk.dev.{read,write}[devname], return (devname, 'read'|'write')."""
    m = re.match(
        r"disk\.dev\.(read|write)\[([^\]]+)\]",
        name.strip(),
    )
    if not m:
        return None
    direction, dev = m.group(1), m.group(2)
    return dev, direction


def _disk_dev_sample_key(dev: str, direction: str) -> str:
    safe = re.sub(r"[^\w\-]+", "_", dev.strip()).strip("_") or "dev"
    return f"disk_dev_{safe}_{direction}_kbps"


def _bytes_rate_to_kbps(raw: float) -> float:
    return round(raw / BYTES_PER_KB, 3)


def _summarize_network(
    samples: list[dict[str, Any]], metric_names: list[str] | None
) -> dict[str, Any]:
    """Per-interface max/avg in/out KB/s for samples that include network fields."""
    if not metric_names:
        return {}
    pairs: list[tuple[str, str, str]] = []
    for m in metric_names:
        pr = _parse_network_metric(m)
        if pr:
            iface, direction = pr
            pairs.append((m, iface, direction))

    if not pairs:
        return {}

    def avg(xs: Iterable[float]) -> float:
        xs = list(xs)
        return round(sum(xs) / len(xs), 2) if xs else 0.0

    out: dict[str, Any] = {}
    for _mname, iface, direction in pairs:
        jkey = _network_sample_key(iface, direction)
        vals = [float(s[jkey]) for s in samples if jkey in s]
        if not vals:
            continue
        sec = out.setdefault(iface, {})
        sec[f"max_{direction}_kbps"] = round(max(vals), 3)
        sec[f"avg_{direction}_kbps"] = avg(vals)

    return out


def _summarize_disk_devices(
    samples: list[dict[str, Any]], metric_names: list[str] | None
) -> dict[str, Any]:
    """Per-device max/avg read/write KB/s and estimated IOPS."""
    if not metric_names:
        return {}
    pairs: list[tuple[str, str, str]] = []
    for m in metric_names:
        pr = _parse_disk_dev_metric(m)
        if pr:
            dev, direction = pr
            pairs.append((m, dev, direction))

    if not pairs:
        return {}

    def avg(xs: Iterable[float]) -> float:
        xs = list(xs)
        return round(sum(xs) / len(xs), 2) if xs else 0.0

    out: dict[str, Any] = {}
    for _mname, dev, direction in pairs:
        jkey = _disk_dev_sample_key(dev, direction)
        vals = [float(s[jkey]) for s in samples if jkey in s]
        if not vals:
            continue
        sec = out.setdefault(dev, {})
        sec[f"max_{direction}_kbps"] = round(max(vals), 3)
        sec[f"avg_{direction}_kbps"] = avg(vals)
        iops_vals = [round(v / KB_PER_4K_IOP, 2) for v in vals]
        sec[f"max_{direction}_iops_estimated"] = round(max(iops_vals), 2)
        sec[f"avg_{direction}_iops_estimated"] = avg(iops_vals)

    for dev, sec in out.items():
        r_max = sec.get("max_read_iops_estimated", 0.0)
        w_max = sec.get("max_write_iops_estimated", 0.0)
        r_avg = sec.get("avg_read_iops_estimated", 0.0)
        w_avg = sec.get("avg_write_iops_estimated", 0.0)
        sec["max_total_iops_estimated"] = round(r_max + w_max, 2)
        sec["avg_total_iops_estimated"] = round(r_avg + w_avg, 2)

    return out


def _row_to_sample(
    metric_names: list[str],
    values: list[str],
    year_hint: int,
    ts_raw: str,
) -> dict[str, Any] | None:
    iso_ts = _parse_timestamp(ts_raw, year_hint)
    if not iso_ts:
        return None

    parsed: list[float | None] = [_parse_scalar(v) for v in values]
    n = min(len(metric_names), len(parsed))
    by_name: dict[str, float | None] = {
        metric_names[i]: parsed[i] for i in range(n)
    }

    sample: dict[str, Any] = {"timestamp": iso_ts}

    u = by_name.get("kernel.all.cpu.user")
    s = by_name.get("kernel.all.cpu.sys")
    idle = by_name.get("kernel.all.cpu.idle")
    cu = _cpu_util_percent(u, s, idle)
    if cu is not None:
        sample["cpu_utilization_percent"] = cu
    if u is not None:
        sample["cpu_user"] = round(u, 2)
    if s is not None:
        sample["cpu_sys"] = round(s, 2)
    if idle is not None:
        sample["cpu_idle"] = round(idle, 2)

    mu = by_name.get("mem.util.used")
    mf = by_name.get("mem.util.free")
    if mu is not None:
        sample["mem_used_gib"] = round(mu / BYTES_PER_GIB, 2)
    if mf is not None:
        sample["mem_free_gib"] = round(mf / BYTES_PER_GIB, 2)

    dr = by_name.get("disk.all.read")
    dw = by_name.get("disk.all.write")
    if dr is not None:
        sample["disk_all_read_kbps"] = round(dr, 2)
    if dw is not None:
        sample["disk_all_write_kbps"] = round(dw, 2)
    if dr is not None and dw is not None:
        total_kbps = dr + dw
        sample["disk_all_total_kbps"] = round(total_kbps, 2)
        sample["disk_read_iops_estimated"] = _kbps_to_iops_est(dr)
        sample["disk_write_iops_estimated"] = _kbps_to_iops_est(dw)
        # Match legacy reports: total IOPs from combined KB/s before per-direction rounding.
        sample["disk_total_iops_estimated"] = round(
            total_kbps / KB_PER_4K_IOP, 2
        )

    for mname in metric_names:
        pr = _parse_network_metric(mname)
        if pr:
            iface, direction = pr
            raw = by_name.get(mname)
            if raw is not None:
                sample[_network_sample_key(iface, direction)] = _bytes_rate_to_kbps(raw)
            continue
        dd = _parse_disk_dev_metric(mname)
        if dd:
            dev, direction = dd
            raw = by_name.get(mname)
            if raw is not None:
                sample[_disk_dev_sample_key(dev, direction)] = round(raw, 2)

    return sample


def _summarize(samples: list[dict[str, Any]]) -> MetricSummary:
    cpu_utils: list[float] = []
    cpu_user_vals: list[float] = []
    cpu_sys_vals: list[float] = []
    disk_read: list[float] = []
    disk_write: list[float] = []
    disk_total: list[float] = []
    read_iops: list[float] = []
    write_iops: list[float] = []
    total_iops: list[float] = []
    mem_used: list[float] = []
    mem_free: list[float] = []
    cpu_rows = 0
    disk_rows = 0
    mem_rows = 0

    for row in samples:
        if "cpu_utilization_percent" in row:
            cpu_rows += 1
            cpu_utils.append(float(row["cpu_utilization_percent"]))
        if "cpu_user" in row:
            cpu_user_vals.append(float(row["cpu_user"]))
        if "cpu_sys" in row:
            cpu_sys_vals.append(float(row["cpu_sys"]))
        if all(k in row for k in ("disk_all_read_kbps", "disk_all_write_kbps")):
            disk_rows += 1
            dr = float(row["disk_all_read_kbps"])
            dw = float(row["disk_all_write_kbps"])
            disk_read.append(dr)
            disk_write.append(dw)
            dt = float(row.get("disk_all_total_kbps", dr + dw))
            disk_total.append(dt)
            if "disk_read_iops_estimated" in row:
                read_iops.append(float(row["disk_read_iops_estimated"]))
            if "disk_write_iops_estimated" in row:
                write_iops.append(float(row["disk_write_iops_estimated"]))
            if "disk_total_iops_estimated" in row:
                total_iops.append(float(row["disk_total_iops_estimated"]))
        if "mem_used_gib" in row and "mem_free_gib" in row:
            mem_rows += 1
            mem_used.append(float(row["mem_used_gib"]))
            mem_free.append(float(row["mem_free_gib"]))

    def avg(xs: Iterable[float]) -> float:
        xs = list(xs)
        return round(sum(xs) / len(xs), 2) if xs else 0.0

    ms = MetricSummary()
    ms.sample_count_valid_cpu = cpu_rows
    ms.sample_count_valid_disk = disk_rows
    ms.sample_count_valid_mem = mem_rows

    if cpu_utils:
        s = sorted(cpu_utils)
        ms.max_cpu_utilization_percent = round(max(cpu_utils), 2)
        ms.avg_cpu_utilization_percent = avg(cpu_utils)
        ms.p95_cpu_utilization_percent = _percentile(s, 95)
    if cpu_user_vals:
        s = sorted(cpu_user_vals)
        ms.max_cpu_user = round(max(cpu_user_vals), 2)
        ms.avg_cpu_user = avg(cpu_user_vals)
        ms.p95_cpu_user = _percentile(s, 95)
    if cpu_sys_vals:
        s = sorted(cpu_sys_vals)
        ms.max_cpu_sys = round(max(cpu_sys_vals), 2)
        ms.avg_cpu_sys = avg(cpu_sys_vals)
        ms.p95_cpu_sys = _percentile(s, 95)
    if disk_read:
        s = sorted(disk_read)
        ms.max_disk_read_kbps = round(max(disk_read), 2)
        ms.avg_disk_read_kbps = avg(disk_read)
        ms.p95_disk_read_kbps = _percentile(s, 95)
    if disk_write:
        s = sorted(disk_write)
        ms.max_disk_write_kbps = round(max(disk_write), 2)
        ms.avg_disk_write_kbps = avg(disk_write)
        ms.p95_disk_write_kbps = _percentile(s, 95)
    if disk_total:
        s = sorted(disk_total)
        ms.max_disk_total_kbps = round(max(disk_total), 2)
        ms.avg_disk_total_kbps = avg(disk_total)
        ms.p95_disk_total_kbps = _percentile(s, 95)
    if read_iops:
        s = sorted(read_iops)
        ms.max_read_iops_estimated = round(max(read_iops), 2)
        ms.avg_read_iops_estimated = avg(read_iops)
        ms.p95_read_iops_estimated = _percentile(s, 95)
    if write_iops:
        s = sorted(write_iops)
        ms.max_write_iops_estimated = round(max(write_iops), 2)
        ms.avg_write_iops_estimated = avg(write_iops)
        ms.p95_write_iops_estimated = _percentile(s, 95)
    if total_iops:
        s = sorted(total_iops)
        ms.max_iops_estimated = round(max(total_iops), 2)
        ms.avg_iops_estimated = avg(total_iops)
        ms.p95_iops_estimated = _percentile(s, 95)
    if mem_used:
        s = sorted(mem_used)
        ms.max_mem_used_gib = round(max(mem_used), 2)
        ms.avg_mem_used_gib = avg(mem_used)
        ms.p95_mem_used_gib = _percentile(s, 95)
    if mem_free:
        ms.min_mem_free_gib = round(min(mem_free), 2)

    return ms


def parse_pmdumptext_log(
    log_path: Path,
    metric_names: list[str] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    text = log_path.read_text(errors="replace")
    year_hint = _infer_year_hint(log_path)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    samples: list[dict[str, Any]] = []

    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        ts_raw = parts[0]
        values = parts[1:]
        names = metric_names
        if not names:
            names = [f"col{i}" for i in range(len(values))]
        elif len(values) != len(names):
            w = min(len(values), len(names))
            names = names[:w]
            values = values[:w]

        sample = _row_to_sample(names, values, year_hint, ts_raw)
        if sample:
            samples.append(sample)

    summary = _summarize(samples)
    summary_dict = asdict(summary)
    disk_dev_summary = _summarize_disk_devices(samples, metric_names)
    if disk_dev_summary:
        summary_dict["disk_devices"] = disk_dev_summary
    net_summary = _summarize_network(samples, metric_names)
    if net_summary:
        summary_dict["network"] = net_summary
    return samples, summary_dict


def _build_monitoring_doc(
    report: dict[str, Any],
    bench_log: Path | None,
    client_log: Path | None,
) -> dict[str, Any]:
    """Build a standalone monitoring.json with per-host aggregate statistics."""
    mon: dict[str, Any] = {}
    for key, log_path in [("bench", bench_log), ("client", client_log)]:
        metrics = report.get(f"{key}_metrics")
        if not isinstance(metrics, dict):
            continue
        summary = metrics.get("summary")
        if isinstance(summary, dict):
            mon[key] = deepcopy(summary)
    mon["raw_data_files"] = {
        "bench_metrics_log": bench_log.name if bench_log and bench_log.is_file() else "",
        "client_metrics_log": client_log.name if client_log and client_log.is_file() else "",
    }
    return mon


def _build_results_doc(report: dict[str, Any]) -> dict[str, Any]:
    """Build a standalone results.json with benchmark KPIs only (no samples)."""
    res: dict[str, Any] = {}
    for key in (
        "run_id",
        "benchmark_tuning_profile",
        "bench",
        "client",
        "postgresql",
        "hammerdb",
        "results",
        "timing",
    ):
        if key in report:
            res[key] = deepcopy(report[key])
    return res


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--output", required=True, type=Path, help="Output JSON path")
    ap.add_argument(
        "--base-json",
        type=Path,
        help="Existing report JSON to merge into (default: empty object)",
    )
    ap.add_argument(
        "--log",
        type=Path,
        help="pmdumptext sample log from the database/bench host",
    )
    ap.add_argument(
        "--client-log",
        type=Path,
        help="pmdumptext sample log from the load-generator host",
    )
    ap.add_argument(
        "--no-samples",
        action="store_true",
        help="Only write summary (omit per-sample arrays)",
    )
    ap.add_argument(
        "--monitoring-json",
        type=Path,
        help="Write a separate monitoring.json with aggregate PCP statistics (mean/max/p95)",
    )
    ap.add_argument(
        "--results-json",
        type=Path,
        help="Write a separate results.json with benchmark KPIs only (no metric samples)",
    )
    args = ap.parse_args()

    report: dict[str, Any] = {}
    if args.base_json:
        base_path = args.base_json.expanduser().resolve()
        if base_path.is_file():
            report = json.loads(base_path.read_text(encoding="utf-8"))
        else:
            report = {}

    log_path = args.log.expanduser().resolve() if args.log else None
    if log_path and log_path.is_file():
        hm = report.get("bench_metrics")
        names = None
        if isinstance(hm, dict):
            raw = hm.get("pmdumptext_metric_names")
            if isinstance(raw, list):
                names = [str(x) for x in raw]
        samples, summary = parse_pmdumptext_log(log_path, names)
        metrics_block: dict[str, Any] = {
            "samples_log_filename": log_path.name,
            "summary": summary,
        }
        if not args.no_samples:
            metrics_block["samples"] = samples
        bench_metrics = report.get("bench_metrics")
        if isinstance(bench_metrics, dict):
            report["bench_metrics"] = _deep_merge(bench_metrics, metrics_block)
        else:
            report["bench_metrics"] = metrics_block

    client_log_path = (
        args.client_log.expanduser().resolve() if args.client_log else None
    )
    if client_log_path and client_log_path.is_file():
        cm = report.get("client_metrics")
        names_c = None
        if isinstance(cm, dict):
            raw_c = cm.get("pmdumptext_metric_names")
            if isinstance(raw_c, list):
                names_c = [str(x) for x in raw_c]
        samples_c, summary_c = parse_pmdumptext_log(client_log_path, names_c)
        client_block: dict[str, Any] = {
            "samples_log_filename": client_log_path.name,
            "summary": summary_c,
        }
        if not args.no_samples:
            client_block["samples"] = samples_c
        existing_client = report.get("client_metrics")
        if isinstance(existing_client, dict):
            report["client_metrics"] = _deep_merge(existing_client, client_block)
        else:
            report["client_metrics"] = client_block

    _write_json(args.output, report)

    if args.monitoring_json:
        mon = _build_monitoring_doc(report, log_path, client_log_path)
        _write_json(args.monitoring_json, mon)

    if args.results_json:
        res = _build_results_doc(report)
        _write_json(args.results_json, res)


if __name__ == "__main__":
    main()
