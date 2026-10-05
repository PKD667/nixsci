{
  description = "nix-lab: run experiments from a TOML spec on any host and keep their records";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    nix-deploy = {
      url = "github:PKD667/nix-deploy";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, nix-deploy }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});
    in {
      # Wrap a program so it becomes a nix-lab / nix-deploy experiment closure.
      #   mkExperiment pkgs { name = "x"; program = "${drv}/bin/x"; metadata = {...}; }
      lib.mkExperiment = pkgs: import ./nix/mk-experiment.nix { inherit pkgs; };

      packages = forAll (pkgs: rec {
        default = nix-lab;
        # `import lab` with no dependencies: safe to put in any experiment closure.
        lab-py = pkgs.runCommand "lab-py" { } ''
          site=$out/${pkgs.python3.sitePackages}
          mkdir -p $site
          cp -r ${./src/lab} $site/lab
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
          packages = with pkgs.rPackages; [ arrow dplyr ggplot2 jsonlite self.packages.${pkgs.system}.labr ];
        };
        nix-lab = pkgs.python3Packages.buildPythonApplication {
          pname = "nix-lab";
          version = "0.1.0";
          src = self;
          pyproject = true;
          build-system = [ pkgs.python3Packages.setuptools ];
          dependencies = [
            nix-deploy.packages.${pkgs.system}.nix-deploy
            pkgs.python3Packages.pyarrow
          ];
          pythonImportsCheck = [ "nix_lab" "lab" ];
          meta.mainProgram = "nix-lab";
        };
      });

      apps = forAll (pkgs: {
        default = { type = "app"; program = "${self.packages.${pkgs.system}.nix-lab}/bin/nix-lab"; };
      });

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ pkgs.python3 pkgs.python3Packages.pytest pkgs.git ];
          shellHook = ''export PYTHONPATH="$PWD/src:${nix-deploy.outPath}:$PYTHONPATH"'';
        };
      });
    };
}
