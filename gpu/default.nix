# The GPU probe as a nixsci experiment (NVIDIA, x86_64-linux).
#   import ./gpu { pkgs = <nixpkgs with config.allowUnfree = true>; mkExperiment = <lib.mkExperiment pkgs>; }
# gives { probe, driver, experiment }.
{ pkgs, mkExperiment }:
let
  inherit (pkgs) lib;
  cuda = pkgs.cudaPackages;

  # Fat binary: real code for every architecture below plus PTX for the newest, which any later
  # GPU JIT-compiles through the host driver. cuda 12.9's nvcc accepts all of them (checked below).
  archs = [ "52" "60" "70" "75" "80" "86" "89" "90" ];
  gencode = lib.concatMapStringsSep " " (a: "-gencode arch=compute_${a},code=sm_${a}") archs
    + " -gencode arch=compute_90,code=compute_90";

  probe = cuda.backendStdenv.mkDerivation {
    pname = "nixsci-gpu-probe";
    version = "1";
    src = lib.fileset.toSource { root = ./.; fileset = ./probe.cu; };
    nativeBuildInputs = [ cuda.cuda_nvcc ];
    buildInputs = [ cuda.cuda_cudart cuda.libcublas ];
    nativeInstallCheckInputs = [ cuda.cuda_cuobjdump ];
    doInstallCheck = true;
    buildPhase = ''
      runHook preBuild
      nvcc -O2 -std=c++17 -Wno-deprecated-gpu-targets ${gencode} -o nixsci-gpu-probe probe.cu -lcublas
      runHook postBuild
    '';
    installPhase = ''
      runHook preInstall
      install -Dm755 nixsci-gpu-probe $out/bin/nixsci-gpu-probe
      runHook postInstall
    '';
    # nvcc accepting a -gencode is not the same as the binary containing it.
    installCheckPhase = ''
      elf=$(cuobjdump --list-elf $out/bin/nixsci-gpu-probe)
      ptx=$(cuobjdump --list-ptx $out/bin/nixsci-gpu-probe)
      for a in ${toString archs}; do
        echo "$elf" | grep -q "sm_$a" || { echo "missing sm_$a SASS"; exit 1; }
      done
      echo "$ptx" | grep -q "sm_90" || { echo "missing compute_90 PTX"; exit 1; }
    '';
    meta = {
      description = "Prints the GPU's identity and measured GEMM/memory throughput as one JSON object";
      license = lib.licenses.unfree; # links the CUDA runtime and cuBLAS
      mainProgram = "nixsci-gpu-probe";
    };
  };

  driver = pkgs.runCommand "nixsci-gpu-driver-sh" { } ''
    install -Dm755 ${./driver.sh} $out/bin/nixsci-gpu-driver
    patchShebangs $out/bin
  '';

  run = pkgs.writeShellApplication {
    name = "gpu-probe-run";
    runtimeInputs = [ pkgs.coreutils pkgs.gnused pkgs.jq ];
    text = ''
      workdir=''${NIX_DEPLOY_WORKDIR:-$PWD}
      lab=''${NIX_LAB_DIR:-$workdir/lab}
      mkdir -p "$lab"

      # the host's driver libraries, for the probe alone (never for this shell)
      ld=$(${driver}/bin/nixsci-gpu-driver "$workdir")

      code=0
      LD_LIBRARY_PATH=$ld ${probe}/bin/nixsci-gpu-probe > "$lab/gpu.json.tmp" || code=$?
      if ! jq -e 'type == "object"' "$lab/gpu.json.tmp" >/dev/null 2>&1; then
        # the probe died before printing: say so instead of recording nothing
        jq -cn --arg e "probe printed no JSON object (exit $code)" \
          '{schema: 1, device: null, measured: null, errors: [$e]}' > "$lab/gpu.json.tmp"
        [ "$code" -ne 0 ] || code=1
      fi
      mv "$lab/gpu.json.tmp" "$lab/gpu.json"
      cat "$lab/gpu.json"

      # lab/SPEC.md record v1: one line, kind "value", appended in a single write
      line=$(jq -c --arg id "$$-gpu" --arg time "$(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)" \
        '{id: $id, time: $time, name: "gpu", kind: "value", data: ., tags: {}}' "$lab/gpu.json")
      printf '%s\n' "$line" >> "$lab/records.jsonl"
      exit "$code"
    '';
  };
in
{
  inherit probe driver;
  experiment = mkExperiment {
    name = "gpu-probe";
    program = "${run}/bin/gpu-probe-run";
    # a target must declare `driver = "nvidia"` to take this job (deploy Backend._admit)
    resources.driver = "nvidia";
    metadata.gpu-probe = { cuda = cuda.cudaMajorMinorVersion; sm = archs; };
  };
}
