"""Shape of the probe JSON. Run: python3 -m unittest discover -s gpu/tests  (stdlib only).

Set GPU_PROBE_JSON=/path/to/gpu.json to also check a file produced by a real run.
"""
import copy
import json
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from shape import problems  # noqa: E402

REAL = json.loads((HERE / "fixtures" / "gtx980.json").read_text())


def hopper():
    """A synthetic H100-like result: every dtype measured. Invented for the test, not a measurement."""
    d = copy.deepcopy(REAL)
    d["device"].update(name="NVIDIA H100 80GB HBM3", compute_capability="9.0", sm_count=132, mem_bus_bits=5120)
    for t, v in (("tf32_gemm", 400.0), ("fp16_gemm", 700.0), ("bf16_gemm", 690.0)):
        d["measured"][t + "_tflops"] = v
        d["spread"][t] = {"iterations": 500, "median_s": 0.001, "min_over_median": 0.99, "max_over_median": 1.2}
    d["skipped"] = []
    return d


def no_device():
    d = copy.deepcopy(REAL)
    d["device"] = None
    d["device_count"] = 0
    d["measured"] = {k: None for k in d["measured"]}
    d["spread"] = {}
    d["skipped"] = []
    d["errors"] = ["device: cudaGetDeviceCount: cudaErrorInsufficientDriver (...)"]
    return d


class Shape(unittest.TestCase):
    def test_real_gtx980_result_is_valid(self):
        self.assertEqual(problems(REAL), [])
        self.assertEqual(REAL["device"]["compute_capability"], "5.2")
        self.assertIsNone(REAL["measured"]["tf32_gemm_tflops"])  # Maxwell has no TF32: null, not 0

    def test_all_dtypes_measured_is_valid(self):
        self.assertEqual(problems(hopper()), [])

    def test_no_device_is_valid_and_all_null(self):
        d = no_device()
        self.assertEqual(problems(d), [])

    def test_json_is_one_line_and_round_trips(self):
        text = json.dumps(REAL, separators=(",", ":"))
        self.assertEqual(json.loads(text), REAL)

    def test_required_keys_are_required(self):
        for path in (("device",), ("driver_version",), ("runtime_version",), ("measured",), ("method", "gemm_n"),
                     ("method", "bw_convention"), ("units",), ("errors",), ("device", "uuid"), ("device", "mem_bus_bits"),
                     ("measured", "mem_bw_gbps"), ("measured", "bf16_gemm_tflops")):
            d = copy.deepcopy(REAL)
            obj = d
            for p in path[:-1]:
                obj = obj[p]
            del obj[path[-1]]
            self.assertTrue(problems(d), f"deleting {'.'.join(path)} was not noticed")

    def test_unexplained_null_is_refused(self):
        d = copy.deepcopy(REAL)
        d["skipped"] = []  # tf32/fp16/bf16 are null and now nothing says why
        self.assertTrue(any("tf32_gemm" in p for p in problems(d)))

    def test_number_without_spread_is_refused(self):
        d = copy.deepcopy(REAL)
        del d["spread"]["fp32_gemm"]
        self.assertTrue(any("no spread.fp32_gemm" in p for p in problems(d)))

    def test_bad_values_are_refused(self):
        for mutate in (
            lambda d: d["measured"].update(fp32_gemm_tflops=float("nan")),
            lambda d: d["measured"].update(fp32_gemm_tflops=-1.0),
            lambda d: d["measured"].update(fp32_gemm_tflops="3.7"),
            lambda d: d["measured"].update(mem_bw_gbps=0),
            lambda d: d["device"].update(compute_capability="5"),
            lambda d: d["device"].update(vram_bytes=0),
            lambda d: d["spread"]["fp32_gemm"].update(min_over_median=1.5),
            lambda d: d.update(schema=2),
            lambda d: d.update(errors="boom"),
        ):
            d = copy.deepcopy(REAL)
            mutate(d)
            self.assertTrue(problems(d), "accepted a bad value")

    def test_not_an_object(self):
        self.assertTrue(problems([]))

    def test_record_line_carries_the_probe_json(self):
        """lab/SPEC.md v1: the wrapper's record line, rebuilt here the way default.nix builds it."""
        line = json.dumps({"id": "1-gpu", "time": "2026-10-21T09:55:07.794189Z", "name": "gpu", "kind": "value",
                           "data": REAL, "tags": {}}, separators=(",", ":"))
        rec = json.loads(line)
        self.assertEqual((rec["name"], rec["kind"]), ("gpu", "value"))
        self.assertEqual(problems(rec["data"]), [])

    @unittest.skipUnless(os.environ.get("GPU_PROBE_JSON"), "set GPU_PROBE_JSON to check a real run")
    def test_given_file(self):
        self.assertEqual(problems(json.loads(Path(os.environ["GPU_PROBE_JSON"]).read_text())), [])

    @unittest.skipUnless(os.environ.get("GPU_PROBE_LAB_DIR"), "set GPU_PROBE_LAB_DIR to a run's lab/ directory")
    def test_lab_dir_of_a_real_run(self):
        """gpu.json and the `gpu` line of records.jsonl agree, and the line follows lab/SPEC.md v1."""
        import re
        lab = Path(os.environ["GPU_PROBE_LAB_DIR"])
        gpu = json.loads((lab / "gpu.json").read_text())
        lines = [json.loads(l) for l in (lab / "records.jsonl").read_text().splitlines()]
        rec = [r for r in lines if r["name"] == "gpu"]
        self.assertEqual(len(rec), 1)
        r = rec[0]
        self.assertEqual((r["kind"], r["tags"]), ("value", {}))
        self.assertRegex(r["time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$")
        self.assertIsInstance(r["id"], str)
        self.assertEqual(r["data"], gpu)
        self.assertEqual(problems(gpu), [])


if __name__ == "__main__":
    unittest.main()
