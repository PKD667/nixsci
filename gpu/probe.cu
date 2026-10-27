// nixsci GPU probe: which GPU is this, and what does it measure on a fixed workload.
// Prints ONE compact JSON object on stdout. Diagnostics never go to stdout.
// Deps: cudart + cuBLAS only. NVML is deliberately not used (not available at build).
//
// Exit code: 0 whenever a device was usable, even if some dtype is unsupported or failed
// (the failure is then null in `measured` plus an entry in `errors`). 1 only when no
// device is usable; the JSON is still printed so the caller can record why.
#include <cuda_runtime.h>
#include <cublas_v2.h>
#include <cuda_fp16.h>
#include <cuda_bf16.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

static const int GEMM_N_DEFAULT = 8192;
static const double SECONDS_PER_TEST = 2.0;
static const int WARMUP = 3;
static const int BW_ITERS = 20;
static const size_t GIB = size_t(1) << 30;

static std::vector<std::string> errors;
static std::vector<std::string> skipped;  // "name: reason" for tests the hardware cannot run

// ---------------------------------------------------------------- JSON emission
static std::string jstr(const std::string& s) {
  std::string o = "\"";
  for (unsigned char c : s) {
    if (c == '"' || c == '\\') { o += '\\'; o += (char)c; }
    else if (c < 0x20) { char b[8]; snprintf(b, sizeof b, "\\u%04x", c); o += b; }
    else o += (char)c;
  }
  return o + "\"";
}
static std::string jnum(double v) {  // never emit NaN/inf: not JSON
  if (!std::isfinite(v)) return "null";
  char b[48];
  snprintf(b, sizeof b, "%.6g", v);
  return b;
}
static std::string jint(long long v) { return std::to_string(v); }
static std::string jlist(const std::vector<std::string>& v) {
  std::string o = "[";
  for (size_t i = 0; i < v.size(); i++) o += (i ? "," : "") + jstr(v[i]);
  return o + "]";
}

// ---------------------------------------------------------------- error helpers
static void note_error(const std::string& where, const std::string& what) {
  errors.push_back(where + ": " + what);
}
#define CUDA_TRY(where, call)                                                   \
  do {                                                                          \
    cudaError_t e__ = (call);                                                   \
    if (e__ != cudaSuccess) {                                                   \
      note_error(where, std::string(#call) + ": " + cudaGetErrorName(e__) +     \
                            " (" + cudaGetErrorString(e__) + ")");              \
      cudaGetLastError();                                                       \
      return false;                                                             \
    }                                                                           \
  } while (0)
#define BLAS_TRY(where, call)                                                   \
  do {                                                                          \
    cublasStatus_t s__ = (call);                                                \
    if (s__ != CUBLAS_STATUS_SUCCESS) {                                         \
      note_error(where, std::string(#call) + ": cublas status " +              \
                            std::to_string((int)s__));                          \
      return false;                                                             \
    }                                                                           \
  } while (0)

// ---------------------------------------------------------------- timing
struct Stat {
  bool ok = false;
  double median = 0, min = 0, max = 0;  // seconds per call
  int iters = 0;
};
static Stat summarize(std::vector<double> t) {
  Stat s;
  if (t.empty()) return s;
  std::sort(t.begin(), t.end());
  size_t n = t.size();
  s.median = n % 2 ? t[n / 2] : 0.5 * (t[n / 2 - 1] + t[n / 2]);
  s.min = t.front();
  s.max = t.back();
  s.iters = (int)n;
  s.ok = s.median > 0;
  return s;
}

// Runs `fn` (enqueues one operation on `stream`): WARMUP calls, one synchronised calibration
// call, then rounds of back-to-back calls each bracketed by its own cudaEvent pair until the
// summed GPU time reaches `seconds`. Returns per-call GPU times.
template <class F>
static bool time_loop(const char* where, cudaStream_t stream, F fn, double seconds,
                      int fixed_iters, std::vector<double>& out) {
  for (int i = 0; i < WARMUP; i++)
    if (!fn()) return false;
  CUDA_TRY(where, cudaStreamSynchronize(stream));
  cudaEvent_t a, b;
  CUDA_TRY(where, cudaEventCreate(&a));
  CUDA_TRY(where, cudaEventCreate(&b));
  cudaEventRecord(a, stream);
  bool ok = fn();
  cudaEventRecord(b, stream);
  if (!ok) { cudaEventDestroy(a); cudaEventDestroy(b); return false; }
  CUDA_TRY(where, cudaEventSynchronize(b));
  float ms0 = 0;
  cudaEventElapsedTime(&ms0, a, b);
  cudaEventDestroy(a);
  cudaEventDestroy(b);
  double t0 = std::max(1e-6, ms0 * 1e-3);

  double total = 0;
  for (int round = 0; round < 6; round++) {
    int n = fixed_iters ? fixed_iters : (int)std::min(20000.0, std::max(3.0, std::ceil((seconds - total) / t0)));
    std::vector<cudaEvent_t> ev(n + 1);
    for (auto& e : ev)
      if (cudaEventCreate(&e) != cudaSuccess) { note_error(where, "cudaEventCreate failed"); return false; }
    cudaEventRecord(ev[0], stream);
    for (int i = 0; i < n; i++) {
      if (!fn()) { for (auto e : ev) cudaEventDestroy(e); return false; }
      cudaEventRecord(ev[i + 1], stream);
    }
    CUDA_TRY(where, cudaEventSynchronize(ev[n]));
    for (int i = 0; i < n; i++) {
      float ms = 0;
      cudaEventElapsedTime(&ms, ev[i], ev[i + 1]);
      out.push_back(ms * 1e-3);
      total += ms * 1e-3;
    }
    for (auto e : ev) cudaEventDestroy(e);
    if (fixed_iters || total >= seconds) break;
  }
  return true;
}

// ---------------------------------------------------------------- data
template <class T> __device__ T from_float(float v);
template <> __device__ float from_float<float>(float v) { return v; }
template <> __device__ __half from_float<__half>(float v) { return __float2half(v); }
template <> __device__ __nv_bfloat16 from_float<__nv_bfloat16>(float v) { return __float2bfloat16(v); }

// Pseudo-random values in [-0.5, 0.5): non-zero, non-denormal, so power draw is not
// artificially low (all-zero operands make some GPUs run faster than real data would).
template <class T>
__global__ void fill(T* p, size_t n, uint32_t seed) {
  size_t i = blockIdx.x * (size_t)blockDim.x + threadIdx.x;
  size_t stride = (size_t)gridDim.x * blockDim.x;
  for (; i < n; i += stride) {
    uint32_t x = (uint32_t)i * 2654435761u ^ seed;
    x ^= x >> 15; x *= 2246822519u; x ^= x >> 13; x *= 3266489917u; x ^= x >> 16;
    p[i] = from_float<T>((float)(x >> 8) * (1.0f / 16777216.0f) - 0.5f);
  }
}

struct GemmCase {
  const char* key;       // fp32_gemm ...
  cudaDataType type;     // A, B, C type
  cublasComputeType_t compute;
  int elem;              // bytes
  int min_cc;            // major*10+minor
  const char* why;       // reason when skipped
};

struct Result {
  bool ok = false;
  double value = 0;  // TFLOPS for GEMM, GB/s for bandwidth
  Stat st;
};

template <class T>
static bool run_gemm(const GemmCase& c, int n, cublasHandle_t h, cudaStream_t stream, Result& r) {
  size_t cnt = (size_t)n * n;
  T *A = nullptr, *B = nullptr, *C = nullptr;
  auto cleanup = [&]() { cudaFree(A); cudaFree(B); cudaFree(C); };
  bool ok = false;
  do {
    if (cudaMalloc(&A, cnt * sizeof(T)) != cudaSuccess || cudaMalloc(&B, cnt * sizeof(T)) != cudaSuccess ||
        cudaMalloc(&C, cnt * sizeof(T)) != cudaSuccess) {
      cudaGetLastError();
      note_error(c.key, "cudaMalloc failed for n=" + std::to_string(n));
      break;
    }
    fill<T><<<1024, 256, 0, stream>>>(A, cnt, 1u);
    fill<T><<<1024, 256, 0, stream>>>(B, cnt, 2u);
    cudaMemsetAsync(C, 0, cnt * sizeof(T), stream);
    cudaError_t e = cudaStreamSynchronize(stream);
    if (e != cudaSuccess) {
      note_error(c.key, std::string("init kernels: ") + cudaGetErrorString(e));
      cudaGetLastError();
      break;
    }
    // alpha = 1, beta = 0 as float: all computeTypes used here take float scalars.
    const float alpha = 1.f, beta = 0.f;
    auto fn = [&]() -> bool {
      cublasStatus_t s = cublasGemmEx(h, CUBLAS_OP_N, CUBLAS_OP_N, n, n, n, &alpha, A, c.type, n, B, c.type, n,
                                      &beta, C, c.type, n, c.compute, CUBLAS_GEMM_DEFAULT);
      if (s != CUBLAS_STATUS_SUCCESS) {
        note_error(c.key, "cublasGemmEx status " + std::to_string((int)s));
        return false;
      }
      return true;
    };
    std::vector<double> t;
    if (!time_loop(c.key, stream, fn, SECONDS_PER_TEST, 0, t)) break;
    r.st = summarize(t);
    if (!r.st.ok) { note_error(c.key, "no timing obtained"); break; }
    r.value = 2.0 * (double)n * n * n / r.st.median / 1e12;
    ok = std::isfinite(r.value);
  } while (0);
  cleanup();
  r.ok = ok;
  return ok;
}

static bool run_bandwidth(size_t free_bytes, cudaStream_t stream, size_t& bytes_used, Result& r) {
  size_t bytes = std::min(GIB, (free_bytes / 4) & ~((size_t(1) << 20) - 1));
  bytes_used = bytes;
  if (bytes < (size_t(1) << 20)) { note_error("mem_bw", "less than 4 MiB free"); return false; }
  void *src = nullptr, *dst = nullptr;
  bool ok = false;
  do {
    if (cudaMalloc(&src, bytes) != cudaSuccess || cudaMalloc(&dst, bytes) != cudaSuccess) {
      cudaGetLastError();
      note_error("mem_bw", "cudaMalloc failed");
      break;
    }
    cudaMemsetAsync(src, 0x5a, bytes, stream);
    cudaMemsetAsync(dst, 0, bytes, stream);
    auto fn = [&]() -> bool {
      cudaError_t e = cudaMemcpyAsync(dst, src, bytes, cudaMemcpyDeviceToDevice, stream);
      if (e != cudaSuccess) { note_error("mem_bw", cudaGetErrorString(e)); cudaGetLastError(); return false; }
      return true;
    };
    std::vector<double> t;
    if (!time_loop("mem_bw", stream, fn, 0, BW_ITERS, t)) break;
    r.st = summarize(t);
    if (!r.st.ok) { note_error("mem_bw", "no timing obtained"); break; }
    // read + write: 2 * bytes of traffic per copy; decimal GB.
    r.value = 2.0 * (double)bytes / r.st.median / 1e9;  // reused field: GB/s
    ok = std::isfinite(r.value);
  } while (0);
  cudaFree(src);
  cudaFree(dst);
  r.ok = ok;
  return ok;
}

static std::string uuid_string(const cudaUUID_t& u) {
  char b[64];
  const unsigned char* p = (const unsigned char*)u.bytes;
  snprintf(b, sizeof b, "GPU-%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x", p[0], p[1], p[2],
           p[3], p[4], p[5], p[6], p[7], p[8], p[9], p[10], p[11], p[12], p[13], p[14], p[15]);
  return b;
}

// The version is the first token after "Kernel Module" that starts with a digit: the line reads
// "... UNIX x86_64 Kernel Module  580.173.02  Tue ..." or "... UNIX Open Kernel Module for x86_64  580.178.04  Release ...".
static std::string kernel_module_version_of(const std::string& line) {
  size_t k = line.find("Kernel Module");
  if (k == std::string::npos) return "";
  size_t i = k + 13;
  while (i < line.size()) {
    i = line.find_first_not_of(' ', i);
    if (i == std::string::npos) return "";
    size_t e = line.find(' ', i);
    std::string tok = line.substr(i, e == std::string::npos ? e : e - i);
    if (isdigit((unsigned char)tok[0])) return tok;
    if (e == std::string::npos) return "";
    i = e;
  }
  return "";
}

static std::string kernel_module_version() {  // from /proc/driver/nvidia/version, if readable
  std::ifstream f("/proc/driver/nvidia/version");
  std::string line;
  if (!f || !std::getline(f, line)) return "";
  return kernel_module_version_of(line);
}

static const char* CLOCKS_NOTE =
    "clock_khz and mem_clock_khz are cudaDevAttrClockRate / cudaDevAttrMemoryClockRate: the nominal "
    "maximum reported by the driver, NOT the clock sustained during the tests. Power, thermal state "
    "and boost behaviour are not observed.";

int main() {
  const char* idx_env = getenv("NIXSCI_GPU_INDEX");
  int index = 0;
  if (idx_env && *idx_env) {
    char* end = nullptr;
    long v = strtol(idx_env, &end, 10);
    if (*end || v < 0 || v > 4096) { note_error("device", std::string("bad NIXSCI_GPU_INDEX=") + idx_env); v = -1; }
    index = (int)v;
  }

  int drv = 0, rt = 0, count = 0, cublas_ver = 0;
  cudaDriverGetVersion(&drv);
  cudaRuntimeGetVersion(&rt);
  cudaGetLastError();

  std::string device_json = "null", measured, spread;
  int n_used = 0;
  size_t bw_bytes = 0;
  bool usable = false;

  cudaError_t ce = cudaGetDeviceCount(&count);
  if (ce != cudaSuccess) {
    note_error("device", std::string("cudaGetDeviceCount: ") + cudaGetErrorName(ce) + " (" + cudaGetErrorString(ce) + ")");
    cudaGetLastError();
    count = 0;
  } else if (count == 0) {
    note_error("device", "no CUDA device visible");
  } else if (index >= count || index < 0) {
    if (index >= 0) note_error("device", "NIXSCI_GPU_INDEX=" + std::to_string(index) + " but only " + std::to_string(count) + " device(s)");
  }

  std::vector<std::pair<std::string, Result>> res;

  if (count > 0 && index >= 0 && index < count) {
    cudaDeviceProp prop;
    int cc_major = 0, cc_minor = 0, sm = 0, clk = 0, mclk = 0, bus = 0;
    bool props = cudaGetDeviceProperties(&prop, index) == cudaSuccess;
    if (!props) { note_error("device", "cudaGetDeviceProperties failed"); cudaGetLastError(); }
    cudaDeviceGetAttribute(&cc_major, cudaDevAttrComputeCapabilityMajor, index);
    cudaDeviceGetAttribute(&cc_minor, cudaDevAttrComputeCapabilityMinor, index);
    cudaDeviceGetAttribute(&sm, cudaDevAttrMultiProcessorCount, index);
    cudaDeviceGetAttribute(&clk, cudaDevAttrClockRate, index);
    cudaDeviceGetAttribute(&mclk, cudaDevAttrMemoryClockRate, index);
    cudaDeviceGetAttribute(&bus, cudaDevAttrGlobalMemoryBusWidth, index);
    cudaGetLastError();
    int cc = cc_major * 10 + cc_minor;

    if (props && cudaSetDevice(index) == cudaSuccess && cudaFree(0) == cudaSuccess) {
      usable = true;
      size_t freeb = 0, totalb = 0;
      cudaMemGetInfo(&freeb, &totalb);
      device_json = "{\"index\":" + jint(index) + ",\"name\":" + jstr(prop.name) + ",\"uuid\":" + jstr(uuid_string(prop.uuid)) +
                    ",\"compute_capability\":" + jstr(std::to_string(cc_major) + "." + std::to_string(cc_minor)) +
                    ",\"vram_bytes\":" + jint((long long)(totalb ? totalb : prop.totalGlobalMem)) +
                    ",\"sm_count\":" + jint(sm) + ",\"clock_khz\":" + jint(clk) + ",\"mem_clock_khz\":" + jint(mclk) +
                    ",\"mem_bus_bits\":" + jint(bus) + "}";

      // Largest n in {8192, 7168, ...} (steps of 1024) such that A, B, C of the widest dtype
      // (fp32) take at most 1/3 of the currently free memory. 3 matrices x 3 = the "3x" margin.
      n_used = GEMM_N_DEFAULT;
      while (n_used >= 1024 && 3.0 * 3.0 * 4.0 * (double)n_used * n_used > (double)freeb) n_used -= 1024;

      cudaStream_t stream;
      cublasHandle_t h = nullptr;
      bool have = cudaStreamCreate(&stream) == cudaSuccess;
      if (!have) note_error("setup", "cudaStreamCreate failed");
      if (have && cublasCreate(&h) != CUBLAS_STATUS_SUCCESS) { note_error("setup", "cublasCreate failed"); have = false; h = nullptr; }
      if (have) {
        cublasGetVersion(h, &cublas_ver);
        cublasSetStream(h, stream);
        cublasSetMathMode(h, CUBLAS_DEFAULT_MATH);
      }

      const GemmCase cases[] = {
          {"fp32_gemm", CUDA_R_32F, CUBLAS_COMPUTE_32F, 4, 50, ""},
          {"tf32_gemm", CUDA_R_32F, CUBLAS_COMPUTE_32F_FAST_TF32, 4, 80, "TF32 tensor cores need compute capability >= 8.0"},
          {"fp16_gemm", CUDA_R_16F, CUBLAS_COMPUTE_32F, 2, 53, "native fp16 needs compute capability >= 5.3"},
          {"bf16_gemm", CUDA_R_16BF, CUBLAS_COMPUTE_32F, 2, 80, "bf16 needs compute capability >= 8.0"},
      };
      for (const auto& c : cases) {
        Result r;
        if (cc < c.min_cc) {
          skipped.push_back(std::string(c.key) + ": " + c.why);
        } else if (!have) {
          // setup error already recorded
        } else if (n_used < 1024) {
          note_error(c.key, "not enough free VRAM for even n=1024");
        } else if (c.type == CUDA_R_32F) {
          run_gemm<float>(c, n_used, h, stream, r);
        } else if (c.type == CUDA_R_16F) {
          run_gemm<__half>(c, n_used, h, stream, r);
        } else {
          run_gemm<__nv_bfloat16>(c, n_used, h, stream, r);
        }
        res.push_back({c.key, r});
      }

      Result bw;
      cudaMemGetInfo(&freeb, &totalb);
      if (have) run_bandwidth(freeb, stream, bw_bytes, bw);
      res.push_back({"mem_bw", bw});
      if (h) cublasDestroy(h);
      if (have) cudaStreamDestroy(stream);
    } else if (props) {
      note_error("device", "cannot initialise device " + std::to_string(index));
      cudaGetLastError();
    }
  }

  // ---- assemble
  auto find = [&](const char* k) -> const Result* {
    for (auto& p : res) if (p.first == k) return &p.second;
    return nullptr;
  };
  auto val = [&](const char* k) {
    const Result* r = find(k);
    return r && r->ok ? jnum(r->value) : std::string("null");
  };
  measured = "{\"fp32_gemm_tflops\":" + val("fp32_gemm") + ",\"tf32_gemm_tflops\":" + val("tf32_gemm") +
             ",\"fp16_gemm_tflops\":" + val("fp16_gemm") + ",\"bf16_gemm_tflops\":" + val("bf16_gemm") +
             ",\"mem_bw_gbps\":" + val("mem_bw") + "}";
  spread = "{";
  bool first = true;
  for (auto& p : res) {
    if (!p.second.ok) continue;
    const Stat& s = p.second.st;
    spread += std::string(first ? "" : ",") + jstr(p.first) + ":{\"iterations\":" + jint(s.iters) +
              ",\"median_s\":" + jnum(s.median) + ",\"min_over_median\":" + jnum(s.min / s.median) +
              ",\"max_over_median\":" + jnum(s.max / s.median) + "}";
    first = false;
  }
  spread += "}";

  std::string out = "{\"schema\":1,\"device\":" + device_json + ",\"device_count\":" + jint(count) +
                    ",\"driver_version\":" + jint(drv) + ",\"runtime_version\":" + jint(rt) +
                    ",\"cublas_version\":" + jint(cublas_ver);
  std::string km = kernel_module_version();
  out += ",\"nvidia_kernel_module\":" + (km.empty() ? std::string("null") : jstr(km));
  out += ",\"measured\":" + measured + ",\"spread\":" + spread;
  out += ",\"method\":{\"gemm_n\":" + (n_used ? jint(n_used) : std::string("null")) +
         ",\"seconds_per_test\":" + jnum(SECONDS_PER_TEST) + ",\"warmup\":" + jint(WARMUP) +
         ",\"gemm\":\"cublasGemmEx, column-major, no transpose, alpha=1 beta=0, CUBLAS_GEMM_DEFAULT, uniform data in [-0.5,0.5); "
         "fp32=CUBLAS_COMPUTE_32F, tf32=CUBLAS_COMPUTE_32F_FAST_TF32, fp16/bf16 in and out with fp32 accumulate\""
         ",\"flop_convention\":\"2*n^3 per GEMM\",\"time_statistic\":\"median of per-call cudaEvent times\""
         ",\"bw_bytes\":" + (bw_bytes ? jint((long long)bw_bytes) : std::string("null")) + ",\"bw_iterations\":" + jint(BW_ITERS) +
         ",\"bw_convention\":\"GB/s = 2 * bytes / median_time / 1e9: a device-to-device cudaMemcpy of bytes reads bytes and writes bytes\""
         ",\"clocks_note\":" + jstr(CLOCKS_NOTE) + "}";
  out += ",\"units\":{\"tflops\":\"1e12 FLOP/s (decimal)\",\"gbps\":\"1e9 bytes/s (decimal)\","
         "\"sizes\":\"vram_bytes and bw_bytes are bytes; the 1 GiB bandwidth buffer is 2^30 bytes (binary)\","
         "\"clock\":\"kHz\",\"version\":\"driver_version and runtime_version: CUDA integer 1000*major+10*minor (12090 = 12.9); cublas_version: 10000*major+100*minor+patch\"}";
  out += ",\"skipped\":" + jlist(skipped) + ",\"errors\":" + jlist(errors) + "}";
  puts(out.c_str());
  fflush(stdout);
  return usable ? 0 : 1;
}
