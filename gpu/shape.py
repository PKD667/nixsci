"""The shape of the probe's JSON (gpu/probe.cu), for readers on the lab side. Stdlib only.

    from shape import problems
    assert problems(json.loads(text)) == []     # [] = the keys and types a reader may rely on

`problems` returns human-readable strings. It checks types, positivity, and the one semantic rule
that makes a null trustworthy: every null in `measured` is explained by a `skipped` or `errors`
entry that names the test, and every number has its `spread` entry.
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

TESTS = ("fp32_gemm", "tf32_gemm", "fp16_gemm", "bf16_gemm", "mem_bw")
MEASURED_KEY = {t: (t + "_tflops" if t.endswith("gemm") else "mem_bw_gbps") for t in TESTS}
DEVICE_INT = ("index", "vram_bytes", "sm_count", "clock_khz", "mem_clock_khz", "mem_bus_bits")


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def problems(d) -> list[str]:
    out: list[str] = []
    if not isinstance(d, dict):
        return ["top level is not an object"]

    def need(obj, key, test, what, where=""):
        if key not in obj:
            out.append(f"missing {where}{key}")
            return False
        if not test(obj[key]):
            out.append(f"{where}{key} is not {what}: {obj[key]!r}")
            return False
        return True

    need(d, "schema", lambda v: v == 1, "1")
    for k in ("device_count", "driver_version", "runtime_version", "cublas_version"):
        need(d, k, lambda v: _int(v) and v >= 0, "a non-negative integer")
    need(d, "nvidia_kernel_module", lambda v: v is None or isinstance(v, str), "a string or null")
    for k in ("skipped", "errors"):
        need(d, k, lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v), "a list of strings")

    dev = d.get("device", "<missing>")
    if dev == "<missing>":
        out.append("missing device")
    elif dev is not None:
        if not isinstance(dev, dict):
            out.append("device is neither an object nor null")
        else:
            for k in DEVICE_INT:
                need(dev, k, lambda v: _int(v) and v >= 0, "a non-negative integer", "device.")
            need(dev, "vram_bytes", lambda v: _int(v) and v > 0, "positive", "device.")
            need(dev, "name", lambda v: isinstance(v, str) and v != "", "a non-empty string", "device.")
            need(dev, "uuid", lambda v: isinstance(v, str) and v.startswith("GPU-"), "a GPU-... string", "device.")
            need(dev, "compute_capability", lambda v: isinstance(v, str) and re.fullmatch(r"\d+\.\d+", v), "'major.minor'", "device.")

    errors, skipped = d.get("errors", []), d.get("skipped", [])
    measured, spread = d.get("measured"), d.get("spread")
    if isinstance(measured, dict) and isinstance(spread, dict) and isinstance(errors, list) and isinstance(skipped, list):
        for t in TESTS:
            k = MEASURED_KEY[t]
            if k not in measured:
                out.append(f"missing measured.{k}")
                continue
            v = measured[k]
            if v is None:
                if dev is not None and not any(s.startswith(t) for s in list(errors) + list(skipped)):
                    out.append(f"measured.{k} is null with no skipped/errors entry naming {t}")
                if t in spread:
                    out.append(f"spread.{t} present but measured.{k} is null")
            elif not _num(v) or v <= 0:
                out.append(f"measured.{k} is not a positive finite number: {v!r}")
            else:
                s = spread.get(t)
                if not isinstance(s, dict):
                    out.append(f"measured.{k} has no spread.{t}")
                else:
                    need(s, "iterations", lambda x: _int(x) and x >= 1, "a positive integer", f"spread.{t}.")
                    need(s, "median_s", lambda x: _num(x) and x > 0, "positive", f"spread.{t}.")
                    if need(s, "min_over_median", _num, "a number", f"spread.{t}.") and s["min_over_median"] > 1:
                        out.append(f"spread.{t}.min_over_median > 1")
                    if need(s, "max_over_median", _num, "a number", f"spread.{t}.") and s["max_over_median"] < 1:
                        out.append(f"spread.{t}.max_over_median < 1")
        extra = set(measured) - {MEASURED_KEY[t] for t in TESTS}
        if extra:
            out.append(f"unknown measured keys {sorted(extra)} (readers may ignore them, writers should announce them)")
    else:
        out.append("measured and spread must be objects")

    m = d.get("method")
    if not isinstance(m, dict):
        out.append("method is not an object")
    else:
        need(m, "gemm_n", lambda v: v is None or (_int(v) and v >= 1024), "null or an integer >= 1024", "method.")
        need(m, "seconds_per_test", lambda v: _num(v) and v > 0, "positive", "method.")
        need(m, "warmup", lambda v: _int(v) and v >= 0, "a non-negative integer", "method.")
        need(m, "bw_bytes", lambda v: v is None or (_int(v) and v > 0), "null or positive", "method.")
        for k in ("bw_convention", "clocks_note", "gemm", "flop_convention", "time_statistic"):
            need(m, k, lambda v: isinstance(v, str) and v != "", "a non-empty string", "method.")
    need(d, "units", lambda v: isinstance(v, dict) and all(isinstance(x, str) for x in v.values()) and v, "a non-empty object of strings")
    return out


def lab_dir_problems(lab) -> list[str]:
    """Check a run's lab/ directory: gpu.json is well formed, and it is the one `gpu` line of records.jsonl,
    which follows lab/SPEC.md v1 (a value record with no tags and a microsecond UTC time)."""
    lab = Path(lab)
    gpu = json.loads((lab / "gpu.json").read_text())
    lines = [json.loads(line) for line in (lab / "records.jsonl").read_text().splitlines()]
    records = [r for r in lines if r.get("name") == "gpu"]
    if len(records) != 1:
        return [f"records.jsonl has {len(records)} gpu lines, not 1"]
    r, out = records[0], problems(gpu)
    if (r.get("kind"), r.get("tags")) != ("value", {}):
        out.append("the gpu record is not a value with no tags")
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z", str(r.get("time"))):
        out.append("the gpu record time is not a microsecond UTC timestamp")
    if not isinstance(r.get("id"), str):
        out.append("the gpu record id is not a string")
    if r.get("data") != gpu:
        out.append("the gpu record differs from gpu.json")
    return out


if __name__ == "__main__":  # python -m shape RUN/lab: the check of a real run, exit 1 on any problem
    found = lab_dir_problems(sys.argv[1])
    print("\n".join(found) or "ok")
    sys.exit(1 if found else 0)
