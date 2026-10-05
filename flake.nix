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
      apps = forAll (pkgs: {
        default = { type = "app"; program = "${self.packages.${pkgs.system}.nix-deploy}/bin/nix-deploy"; };
      });
    };
}
