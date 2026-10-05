{
  description = "nixsci: a science stack on Nix - deploy, lab, work";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});

      # One python library (with its CLI) per distribution in this repository.
      distributions = py: {
        nixsci-deploy = py.buildPythonPackage {
          pname = "nixsci-deploy";
          version = "0.1.0";
          src = ./deploy;
          pyproject = true;
          build-system = [ py.setuptools ];
          pythonImportsCheck = [ "nixsci.deploy" ];
          meta.mainProgram = "nix-deploy";
        };
        nixsci-lab = py.buildPythonPackage {
          pname = "nixsci-lab";
          version = "0.1.0";
          src = ./lab;
          pyproject = true;
          build-system = [ py.setuptools ];
          dependencies = [ py.nixsci-deploy py.pyarrow ];
          pythonImportsCheck = [ "nixsci.lab" ];
          meta.mainProgram = "nix-lab";
        };
      };
    in {
      # `pkgs.python3.withPackages (p: [ p.nixsci-deploy ])` in any consumer that applies this.
      overlays.default = final: prev: {
        pythonPackagesExtensions = prev.pythonPackagesExtensions ++ [
          (pyfinal: pyprev: distributions pyfinal)
        ];
      };

      # Wrap a program so it becomes a nixsci experiment closure.
      #   mkExperiment pkgs { name = "x"; program = "${drv}/bin/x"; metadata = {...}; }
      lib.mkExperiment = pkgs: import ./lab/nix/mk-experiment.nix { inherit pkgs; };

      packages = forAll (pkgs:
        let
          pyl = pkgs.python3.override {
            packageOverrides = pyfinal: pyprev: distributions pyfinal;
          };
          pp = pyl.pkgs;
        in rec {
          default = deploy;
          deploy = pp.nixsci-deploy;
          lab = pp.nixsci-lab;
          # `from nixsci import lab` with no dependencies: safe to put in any experiment closure.
          lab-py = pkgs.runCommand "nixsci-lab-py" { } ''
            site=$out/${pkgs.python3.sitePackages}/nixsci
            mkdir -p $site
            cp -r ${./lab/src/nixsci/lab} $site/lab
          '';
          # R package that reads collected runs and compacted datasets.
          labr = pkgs.rPackages.buildRPackage {
            pname = "labr";
            version = "0.1.0";
            src = ./lab/r/labr;
            propagatedBuildInputs = with pkgs.rPackages; [ arrow jsonlite ];
          };
          # The pinned R used by `nix-lab analyze`: Rscript plus the analysis packages.
          r-env = pkgs.rWrapper.override {
            packages = with pkgs.rPackages; [ arrow dplyr ggplot2 jsonlite labr ];
          };
        });

      experiments = forAll (pkgs: {
        # Self-test fixture: a closure that reports where it ran, to exercise any target end to end.
        hello =
          let
            run = pkgs.writeShellApplication {
              name = "hello";
              runtimeInputs = [ pkgs.coreutils pkgs.hostname ];
              text = ''
                echo "hello from $(hostname) arch=$(uname -m) workdir=$NIX_DEPLOY_WORKDIR"
                echo "home: ''${HOME:-unset}"
                echo "enter: ''${NIX_DEPLOY_ENTER:-unset}"
                echo "hosts: ''${NIX_DEPLOY_HOSTFILE:+$(tr '\n' ' ' < "$NIX_DEPLOY_HOSTFILE")}"
                if [ -n "''${NIX_DEPLOY_HOSTFILE:-}" ]; then
                  first=1
                  while read -r host; do
                    if [ "$first" = 1 ]; then first=0; continue; fi
                    # shellcheck disable=SC2086
                    echo "peer $host: $(''${NIX_DEPLOY_RSH:-ssh} "$host" "$NIX_DEPLOY_ENTER" hostname 2>&1 | tail -n 1)"
                  done < "$NIX_DEPLOY_HOSTFILE"
                fi
              '';
            };
          in pkgs.runCommand "hello-experiment" { } ''
            mkdir $out
            echo '{"program": "${run}/bin/hello"}' > $out/experiment.json
          '';
        # Typed loss rows, one curve per run (see lab/examples/demo.toml).
        demo = (import ./lab/nix/mk-experiment.nix { inherit pkgs; }) {
          name = "demo";
          program = "${pkgs.python3}/bin/python";
          args = [ "${./lab/examples/demo.py}" ];
          env.PYTHONPATH = "${self.packages.${pkgs.system}.lab-py}/${pkgs.python3.sitePackages}";
        };
      });

      apps = forAll (pkgs: {
        default = { type = "app"; program = "${self.packages.${pkgs.system}.deploy}/bin/nix-deploy"; };
        lab = { type = "app"; program = "${self.packages.${pkgs.system}.lab}/bin/nix-lab"; };
      });

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ pkgs.python3 pkgs.python3Packages.pytest pkgs.python3Packages.pyarrow pkgs.git ];
          shellHook = ''export PYTHONPATH="$PWD/deploy/src:$PWD/lab/src:$PYTHONPATH"'';
        };
      });
    };
}
