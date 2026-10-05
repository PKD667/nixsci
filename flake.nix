{
  description = "nixsci: a science stack on Nix - deploy, lab, work";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});

      # The python library, with both CLIs (nix-deploy, nix-lab).
      library = py: {
        nixsci = py.buildPythonPackage {
          pname = "nixsci";
          version = "0.1.0";
          src = ./.;
          pyproject = true;
          build-system = [ py.setuptools ];
          dependencies = [ py.pyarrow ];
          pythonImportsCheck = [ "nixsci.deploy" "nixsci.lab" ];
        };
      };
    in {
      # `pkgs.python3.withPackages (p: [ p.nixsci ])` in any consumer that applies this.
      overlays.default = final: prev: {
        pythonPackagesExtensions = prev.pythonPackagesExtensions ++ [
          (pyfinal: pyprev: library pyfinal)
        ];
      };

      # Wrap a program so it becomes a nixsci experiment closure.
      #   mkExperiment pkgs { name = "x"; program = "${drv}/bin/x"; metadata = {...}; }
      lib.mkExperiment = pkgs: import ./nix/mk-experiment.nix { inherit pkgs; };

      packages = forAll (pkgs:
        let pp = (pkgs.python3.override { packageOverrides = pyfinal: pyprev: library pyfinal; }).pkgs; in rec {
          default = nixsci;
          nixsci = pp.nixsci;
          # `from nixsci import lab` with no dependencies: safe to put in any experiment closure.
          lab-py = pkgs.runCommand "nixsci-lab-py" { } ''
            site=$out/${pkgs.python3.sitePackages}/nixsci
            mkdir -p $site
            cp -r ${./nixsci/lab} $site/lab
          '';
          # R package that reads collected runs and compacted datasets.
          labr = pkgs.rPackages.buildRPackage {
            pname = "labr";
            version = "0.1.0";
            src = ./r/labr;
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
        # Typed loss rows, one curve per run (see examples/demo.toml).
        demo = (import ./nix/mk-experiment.nix { inherit pkgs; }) {
          name = "demo";
          program = "${pkgs.python3}/bin/python";
          args = [ "${./examples/demo.py}" ];
          env.PYTHONPATH = "${self.packages.${pkgs.system}.lab-py}/${pkgs.python3.sitePackages}";
        };
      });

      apps = forAll (pkgs: {
        default = { type = "app"; program = "${self.packages.${pkgs.system}.nixsci}/bin/nix-deploy"; };
        lab = { type = "app"; program = "${self.packages.${pkgs.system}.nixsci}/bin/nix-lab"; };
      });

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ pkgs.python3 pkgs.python3Packages.pytest pkgs.python3Packages.pyarrow pkgs.git ];
          shellHook = ''export PYTHONPATH="$PWD:$PYTHONPATH"'';
        };
      });
    };
}
