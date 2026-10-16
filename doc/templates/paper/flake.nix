{
  inputs.nixsci.url = "github:PKD667/nixsci";
  inputs.nixpkgs.follows = "nixsci/nixpkgs";

  outputs = { self, nixsci, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      doc = nixsci.lib.doc { inherit pkgs; };
      # lab = nixsci.lib.lab { inherit pkgs; project = self; };  # when the figures are computed
      paper = doc.document {
        name = "paper";
        template = doc.templates.paper;
        src = ./tex;
        # include."gen/curve.pdf" = "${lab.analyses.curves}/curve.pdf";
      };
    in {
      packages.${system} = { inherit (paper) source pdf crate zip; default = paper.pdf; };
      devShells.${system}.default = paper.shell;
    };
}
