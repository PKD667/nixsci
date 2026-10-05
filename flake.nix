{
  description = "nixsci: a science stack on Nix - deploy, lab, work";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});

      # The python library and its one executable, `nixsci`.
      library = py: {
        nixsci = py.buildPythonPackage {
          pname = "nixsci";
          version = "0.1.0";
          src = ./.;
          pyproject = true;
          build-system = [ py.setuptools ];
          dependencies = [ py.pyarrow ];
          pythonImportsCheck = [ "nixsci.cli" "nixsci.deploy" "nixsci.lab" ];
          meta.mainProgram = "nixsci";
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
      lib.mkExperiment = pkgs: import ./lab/nix/mk-experiment.nix { inherit pkgs; };
      # Experiments and analyses as Nix values. `project` is the calling flake's `self`.
      #   lab = nixsci.lib.lab { inherit pkgs; project = self; };
      lib.lab = { pkgs, project }:
        import ./lab/nix/lab.nix {
          inherit pkgs project;
          nixsci = self.packages.${pkgs.stdenv.hostPlatform.system}.nixsci;
          python = self.packages.${pkgs.stdenv.hostPlatform.system}.python;
          rEnv = self.packages.${pkgs.stdenv.hostPlatform.system}.r-env;
        };

      packages = forAll (pkgs:
        let pp = (pkgs.python3.override { packageOverrides = pyfinal: pyprev: library pyfinal; }).pkgs; in rec {
          default = nixsci;
          nixsci = pp.nixsci;
          # The interpreter that has nixsci, for derivations that run its modules.
          python = (pkgs.python3.override { packageOverrides = pyfinal: pyprev: library pyfinal; }).withPackages (p: [ p.nixsci ]);
          # An analysis is a derivation: `nix build .#demo-curves`.
          demo-curves = self.lab.${pkgs.stdenv.hostPlatform.system}.analyses.demo-curves;
          # `from nixsci import lab` with no dependencies: safe to put in any experiment closure.
          lab-py = pkgs.runCommand "nixsci-lab-py" { } ''
            site=$out/${pkgs.python3.sitePackages}/nixsci
            mkdir -p $site
            mkdir $site/lab
            cp ${./lab}/*.py $site/lab/
          '';
          # R package for analysis scripts: use("alias"), runs(), params(), out().
          nixsci-r = pkgs.rPackages.buildRPackage {
            pname = "nixsci";
            version = "0.1.0";
            src = ./lab/r;
            propagatedBuildInputs = with pkgs.rPackages; [ arrow jsonlite ];
          };
          # The pinned R used by `nixsci lab analyze`: Rscript plus the analysis packages.
          r-env = pkgs.rWrapper.override {
            packages = with pkgs.rPackages; [ arrow dplyr ggplot2 jsonlite nixsci-r ];
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

      # The demo, written as Nix values: `nix run .#demo`, then `nix build .#demo-curves`.
      lab = forAll (pkgs:
        let
          l = self.lib.lab { inherit pkgs; project = self; };
          system = pkgs.stdenv.hostPlatform.system;
        in rec {
          experiments.demo = l.experiment {
            name = "demo";
            closures.${system} = self.experiments.${system}.demo;
            seeds = [ 0 1 ];
            sweep.epsilon = [ 0.1 0.3 ];
            resources = { provider = "local"; hosts = 2; };
            data.loss.columns = { epoch = "int"; value = "float"; split = "str"; };
          };
          analyses.demo-curves = l.analysis {
            name = "demo-curves";
            use.demo = experiments.demo;
            pipelines.curve.script = ./lab/examples/demo.R;
          };
        });

      apps = forAll (pkgs: {
        default = { type = "app"; program = "${self.packages.${pkgs.system}.nixsci}/bin/nixsci"; };
        demo = self.lab.${pkgs.system}.experiments.demo.run;
      });
      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ pkgs.python3 pkgs.python3Packages.pytest pkgs.python3Packages.hypothesis pkgs.python3Packages.pyarrow pkgs.git ];
          shellHook = ''export PYTHONPATH="$(dirname "$PWD"):$PYTHONPATH"'';
        };
      });
    };
}
