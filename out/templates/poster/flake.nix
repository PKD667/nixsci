{
  inputs.nixsci.url = "github:PKD667/nixsci";
  inputs.nixpkgs.follows = "nixsci/nixpkgs";

  outputs = { self, nixsci, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      out = nixsci.lib.out { inherit pkgs; };
      # lab = nixsci.lib.lab { inherit pkgs; project = self; };  # when the figures are computed
      poster = out.document {
        name = "poster";
        template = out.templates.poster;
        src = ./tex;
        # include."gen/curve.pdf" = "${lab.analyses.curves}/curve.pdf";
      };
    in {
      packages.${system} = { inherit (poster) source pdf crate zip; default = poster.pdf; };
      devShells.${system}.default = poster.shell;
    };
}
