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
          run = pkgs.writeShellScriptBin "hello" ''
            echo "hello from $(hostname) arch=$(uname -m) workdir=$NIX_DEPLOY_WORKDIR"
            echo "enter: ''${NIX_DEPLOY_ENTER:-unset}"
            echo "hosts: ''${NIX_DEPLOY_HOSTFILE:+$(cat "$NIX_DEPLOY_HOSTFILE" | tr '\n' ' ')}"
          '';
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
