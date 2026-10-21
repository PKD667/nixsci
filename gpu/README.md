# gpu

A standard probe that records **which GPU a run got** and **what it measured** on a fixed workload, so runtimes from different GPUs can later be normalised ("standardized GPU time"). NVIDIA only. AMD (ROCm, MI210 at Luxembourg) is **not done**.

Also here: `driver.sh`, which lets a Nix-built CUDA program find the *host's* driver library on non-NixOS machines.

| file | what |
|---|---|
| `probe.cu` | one CUDA C++ program (cudart + cuBLAS), prints one JSON object |
| `default.nix` | builds it (fat binary, sm_52 60 70 75 80 86 89 90 + PTX 9.0) and wraps it as experiment `gpu-probe` |
| `driver.sh` | builds `$NIX_DEPLOY_WORKDIR/driver/lib`, symlinks to the host's driver libs only |
| `shape.py` | `problems(obj) -> [str]`: the keys and types the lab side may rely on (stdlib) |
| `tests/` | `test_driver.sh` (fake host layouts), `test_shape.py` (JSON shape) |

## Run it

```sh
nix build .#experiments.x86_64-linux.gpu-probe          # or .#gpu-probe for the bare binary
nixsci deploy --config targets.json --target box run . gpu-probe r1 --handle r1.json
nixsci deploy --config targets.json --target box fetch r1.json lab ./out     # lab/gpu.json, lab/records.jsonl
```

The tree must be a committed git checkout (deploy refuses a dirty one). The experiment declares `resources.driver = "nvidia"`, so the target must say so, or `_admit` refuses it:

```json
{"targets": {"box": {"backend": "native", "system": "x86_64-linux", "store": "/nix/store",
                     "run_root": "/tmp/runs", "rootless": false, "driver": "nvidia"}}}
```

Pick another GPU of a multi-GPU host with `--env NIXSCI_GPU_INDEX=1` (default 0). One run measures one GPU.

From another flake: `nixsci.lib.gpuProbe pkgs` is `{ probe, driver, experiment }`; `pkgs` must have `config.allowUnfree = true` (CUDA).

What the experiment does, in order: `driver.sh` → probe (with `LD_LIBRARY_PATH` set for that one command) → `$NIX_LAB_DIR/gpu.json` → one line appended to `$NIX_LAB_DIR/records.jsonl` (lab/SPEC.md v1: `{"id","time","name":"gpu","kind":"value","data":<probe JSON>,"tags":{}}`, built with `jq`; lab has no record CLI). Exit status is the probe's: 1 only when no device is usable (the JSON, with its `errors`, is still recorded). The `gpu` value is a nested object, not a flat typed dataset, so it is not compacted to Parquet; read it with `lab.load(run)`. Flattening it into a declared dataset is a lab-side job, not done.

## What it measures

Square GEMM `n = 8192` (smaller in steps of 1024 if 9 × 4 n² bytes exceed free VRAM; `method.gemm_n` says what was used) through `cublasGemmEx`, column-major, no transpose, α=1 β=0, uniform data in [-0.5, 0.5), `CUBLAS_GEMM_DEFAULT`. 3 warm-up calls, then calls timed one by one with `cudaEvent` until ≥ 2 s of GPU time. TFLOPS = 2n³ / **median** time, decimal (1e12). `spread.<test>` gives `min_over_median` and `max_over_median` so noise is visible.

| test | operands, compute | run when |
|---|---|---|
| `fp32_gemm` | fp32, `COMPUTE_32F` (no TF32) | cc ≥ 5.0 |
| `tf32_gemm` | fp32 in/out, `COMPUTE_32F_FAST_TF32` | cc ≥ 8.0 |
| `fp16_gemm` | fp16 in/out, fp32 accumulate | cc ≥ 5.3 |
| `bf16_gemm` | bf16 in/out, fp32 accumulate | cc ≥ 8.0 |
| `mem_bw` | device-to-device `cudaMemcpy` of 1 GiB (or free/4), median of 20 | always |

`mem_bw_gbps = 2 × bytes / median_time / 1e9`: a copy reads `bytes` and writes `bytes` (`method.bw_convention`). A test that is not run for hardware reasons is `null` plus a `skipped` entry; one that fails is `null` plus an `errors` entry. A `null` always has one of the two (`shape.py` checks this). No number is ever made up.

## JSON keys

`schema` (=1) · `device` {`index name uuid compute_capability vram_bytes sm_count clock_khz mem_clock_khz mem_bus_bits`} or `null` · `device_count` · `driver_version` and `runtime_version` (CUDA integer, 13000 = 13.0) · `cublas_version` · `nvidia_kernel_module` (string from `/proc/driver/nvidia/version`, or `null`) · `measured` {`fp32_gemm_tflops tf32_gemm_tflops fp16_gemm_tflops bf16_gemm_tflops mem_bw_gbps`} · `spread` {test: `iterations median_s min_over_median max_over_median`} · `method` {`gemm_n seconds_per_test warmup gemm flop_convention time_statistic bw_bytes bw_iterations bw_convention clocks_note`} · `units` · `skipped` · `errors`. Git and closure identity are not the probe's job: the run manifest has them.

## What it does NOT tell you

- **One scalar does not transfer.** A GPU that is 2× faster on this GEMM is not 2× faster on a memory-bound or latency-bound workload. Normalise a runtime with the number that matches the workload (`fp32_gemm_tflops` for dense fp32, `mem_bw_gbps` for streaming), and expect error otherwise.
- **Clocks are nominal.** `clock_khz`, `mem_clock_khz` are `cudaDevAttrClockRate`/`MemoryClockRate`: the maximum the driver reports, not what the card ran at. On the GTX 980 below the reported memory clock is 3505 MHz while the card ran at 3004 MHz during the test (see below), so peak-from-`mem_clock_khz` overstates bandwidth by 17 %. NVML is not available at build, so sustained clock, power, temperature and throttling are **not observed**.
- **Thermal and power state** at the moment of the run affect the number; one run is a sample. Compare `spread` and repeat.
- **MIG slices, shared or time-sliced GPUs, other tenants on the device:** the probe cannot tell; it reports what it got. Check `nvidia-smi` on the node.
- **cuBLAS picks kernels by heuristic that changes between versions**, and for old architectures (sm_52 is deprecated in CUDA 12.9: the build warns) may not tune at all. Numbers are comparable only at equal `runtime_version` and `cublas_version`.
- One size only (no sweep), and no convolution, attention, FP8, FP64, or multi-GPU/NVLink.
- PTX is compiled for 9.0 and JIT-compiled by the host driver on newer GPUs (Blackwell and later), so the first call there is slower; warm-up absorbs it.

## Measured on a GTX 980

Machine `camarade` (NixOS, kernel 6.18.39, Xeon E5-2640 v3), NVIDIA driver 580.173.02 (CUDA driver API 13.0), CUDA runtime 12.9, cuBLAS 12.9.2.10, 2026-10-21, GPU idle beforehand (6 MiB used, 0 % utilisation). Commit `7f52412` of this branch.

```sh
python -m nixsci.deploy --config targets.json --target camarade run . gpu-probe r1 --handle r1.json
jq -c .measured,.spread lab/gpu.json
```
```
fp32_gemm_tflops 3.70654   (spread min/median 0.9947, max/median 1.0054, 7 calls, n=8192)
mem_bw_gbps      162.098   (spread 0.9993 .. 1.0009, 20 calls, 1032847360 bytes)
tf32, fp16, bf16: null, skipped (cc 5.2: no TF32/bf16; fp16 needs cc ≥ 5.3)
```
Two other runs of the same binary (one directly, one in the fake-Debian demo below) gave 3.74462 and 3.71733 TFLOPS, 162.094 GB/s for the first: run-to-run difference about 1 %.

Against the published peak (GTX 980: 2048 cores × 2 FLOP × 1216 MHz boost = 4.98 TFLOPS; 224 GB/s = 3505 MHz × 2 × 256 bit):

| | measured | peak | fraction |
|---|---|---|---|
| fp32 GEMM | 3.71 – 3.74 TFLOPS | 4.98 (boost 1216 MHz); 4.92 at the 1201 MHz the card actually ran at | 74 – 75 % (76 % of the clock-matched peak) |
| mem copy | 162.1 GB/s | 224 (at 3505 MHz); 192 at the 3004 MHz it actually ran at | 72 % (84 % of the clock-matched peak) |

`nvidia-smi --query-gpu=clocks.sm,clocks.mem,power.draw,clocks_throttle_reasons.active -lms 500` sampled during the run: SM 1201 MHz, memory **3004 MHz** (the driver's compute P-state, not the 3505 MHz the probe reports as nominal), 177 W, 100 % utilisation, throttle reasons `0x0` (none flagged). So the memory gap is mostly the lower memory clock plus the usual ~85 % efficiency of a plain copy. The fp32 gap (24 % below the clock-matched peak) is **not explained**: candidates, none tested, are that cuBLAS 12.9 has little tuning left for sm_52, and that a real GEMM does not keep every FMA issue slot busy. I did not run a competing GEMM implementation to tell which.

Closure (`nix path-info -S -h`): experiment 1.1 GiB (1,139,608,592 bytes; 21 paths), bare probe 1.1 GiB. 816 MiB of that is `libcublas` (cuBLASLt) and 217 MiB its `nvrtc` dependency. Linking cuBLAS statically was tried on a 2-architecture build: 792 MiB closure, so about 28 % smaller; not adopted.

## Driver binding (`driver.sh`)

A Nix binary on Debian cannot find `libcuda.so.1` (the host has no Nix rpath entry for it), and `LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu` would also expose the host's libc to a binary that carries its own. So:

```sh
ld=$(driver.sh [WORKDIR])          # WORKDIR, else $NIX_DEPLOY_WORKDIR, else $PWD
LD_LIBRARY_PATH=$ld program        # for that one command
```

- Searches `/run/opengl-driver/lib`, `/usr/lib/x86_64-linux-gnu`, `/usr/lib/aarch64-linux-gnu`, `/usr/lib64`, `/usr/lib`, `/usr/lib/nvidia`, in that order (`NIXSCI_DRIVER_SEARCH`, colon-separated, replaces the list; the tests use it). The first directory with a 64-bit `libcuda.so.1` is *the* driver directory; every library comes from it, so two driver versions are never mixed.
- Creates `WORKDIR/driver/lib` containing **only symlinks**: `libcuda*`, `libcudadebugger*`, `libnvidia-ml*`, `libnvidia-ptxjitcompiler*`, `libnvidia-nvvm*`, `libnvidia-gpucomp*`, `libnvidia-fatbinaryloader*`, plus any `libnvidia-*`/`libcuda*` that `readelf -d` shows libcuda needing, transitively (when `readelf` exists; the fixed list covers a driver without it). Symlinks from an earlier run are replaced; regular files are never removed. Nothing outside `WORKDIR/driver` is touched.
- stderr: `libX <- dir` for each link. stdout: only the new `LD_LIBRARY_PATH` value (`WORKDIR/driver/lib` prepended to the old one).
- No driver found: stderr `no NVIDIA driver library found on this host`, exit 0, stdout is the old `LD_LIBRARY_PATH` unchanged, nothing created. The probe then reports `cudaGetDeviceCount: ... InsufficientDriver` in `errors`.

Demonstrated here: a fake Debian directory holding the driver symlinks plus a poisoned `libm.so.6`: `LD_LIBRARY_PATH=<that dir>` makes the probe die (`libm.so.6: file too short`); through `driver.sh` it ran and measured. **Not verified on a real Debian or Grid'5000 node:** this machine is NixOS, where the libraries are Nix-built, so the actual host-libc-vs-Nix-libc interplay (the Nix glibc is newer than Debian's; libcuda's `libc.so.6` NEEDED resolves to the already-loaded Nix one) is argued, not observed. On this NixOS machine the probe also finds the driver without `driver.sh` (observed: it loads libcuda with `LD_LIBRARY_PATH` empty, and fails with `InsufficientDriver` when `/run/opengl-driver` is masked by a tmpfs in a mount namespace). Its RUNPATH has no `opengl-driver` entry, so it is the Nix glibc here that searches that directory; I did not look up where that comes from.

## Tests

```sh
bash gpu/tests/test_driver.sh                     # 36 checks on fake Debian/NixOS/no-driver layouts
python3 -m unittest discover -s gpu/tests         # JSON shape (stdlib)
GPU_PROBE_LAB_DIR=<run>/lab python3 -m unittest discover -s gpu/tests   # also check a real run's gpu.json and records.jsonl
```

Both were mutation-checked: 11 deliberate breaks of `driver.sh` (search order swapped, last directory wins, `ln -s` → `cp`, libc added to the list, a write outside the workdir, `LD_LIBRARY_PATH` altered on no driver, message reworded, 32-bit check removed, regular files deleted on rerun, old `LD_LIBRARY_PATH` dropped, NEEDED filter opened) and 8 of `shape.py` each failed at least one test; the files were then restored. The tests check a model of the host (fake directories, a stub `readelf`), not a Debian machine.
