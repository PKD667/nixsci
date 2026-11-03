{
  inputs.nixsci.url = "github:PKD667/nixsci";
  inputs.nixpkgs.follows = "nixsci/nixpkgs";

  outputs = { self, nixsci, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      doc = nixsci.lib.doc { inherit pkgs; };
      # lab = nixsci.lib.lab { inherit pkgs; project = self; };  # when the numbers are computed
      slides = doc.document {
        name = "slides";
        template = doc.templates.slides;
        src = ./tex;
        source = self;   # lets `publish` refuse a tree with uncommitted changes
        # projects = [ (doc.project { name = "p"; values.curves = doc.output lab.analyses.curves "values.json"; }) ];  # \val{p/curves.key}
      };
    in {
      packages.${system} = { inherit (slides) source pdf crate zip; default = slides.pdf; };
      devShells.${system}.default = slides.shell;
    };
}
