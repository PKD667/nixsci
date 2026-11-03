{
  inputs.nixsci.url = "github:PKD667/nixsci";
  inputs.nixpkgs.follows = "nixsci/nixpkgs";

  outputs = { self, nixsci, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      doc = nixsci.lib.doc { inherit pkgs; };
      # lab = nixsci.lib.lab { inherit pkgs; project = self; };  # when the numbers are computed
      poster = doc.document {
        name = "poster";
        template = doc.templates.poster;
        src = ./tex;
        source = self;   # lets `publish` refuse a tree with uncommitted changes
        # projects = [ (doc.project { name = "p"; values.curves = doc.output lab.analyses.curves "values.json"; }) ];  # \val{p/curves.key}
      };
    in {
      packages.${system} = { inherit (poster) source pdf crate zip; default = poster.pdf; };
      devShells.${system}.default = poster.shell;
    };
}
