{
  description = "nix-deploy: run an immutable Nix experiment closure on any host";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});
    in {
      packages = forAll (pkgs: rec {
        default = nix-deploy;
        nix-deploy = pkgs.python3Packages.buildPythonApplication {
          pname = "nix-deploy";
          version = "0.1.0";
          src = self;
          pyproject = true;
          build-system = [ pkgs.python3Packages.setuptools ];
          pythonImportsCheck = [ "nix_deploy" ];
          meta.mainProgram = "nix-deploy";
        };
      });
      # Self-test fixture: a closure that reports where it ran, to exercise any target end to end.
      experiments = nixpkgs.lib.genAttrs systems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
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
        in {
          hello = pkgs.runCommand "hello-experiment" { } ''
            mkdir $out
            echo '{"program": "${run}/bin/hello"}' > $out/experiment.json
          '';
        });

      apps = forAll (pkgs: {
        default = { type = "app"; program = "${self.packages.${pkgs.system}.nix-deploy}/bin/nix-deploy"; };
      });
    };
}
