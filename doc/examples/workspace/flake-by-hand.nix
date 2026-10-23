{
  inputs.nixsci.url = "git+file:///home/pkd/code/nixsci?ref=main";
  inputs.nixpkgs.follows = "nixsci/nixpkgs";

  outputs = { self, nixsci, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      doc = nixsci.lib.doc { inherit pkgs; };

      plot = doc.project {
        name = "plot";
        style = ./plot/plot.sty;
        figures.curve = ./plot/figures/curve.tex;
      };
      house = doc.project {
        name = "house";
        style = ./house/house.sty;
        figures.logo = ./house/figures/logo.tex;
      };

      memo = doc.document {
        name = "notes-memo";
        src = ./notes/memo/tex;
        projects = [ plot house ];
        source = self;
      };
      letter = doc.document {
        name = "notes-letter";
        src = ./notes/letter/tex;
        projects = [ house ];
        source = self;
      };
      documents = { "notes-memo" = memo; "notes-letter" = letter; };
      each = f: nixpkgs.lib.concatMapAttrs f documents;
    in
    {
      packages.${system} = each (n: d: {
        ${n} = d.pdf;
        "${n}-publish" = d.publish;
        "${n}-source" = d.source;
        "${n}-crate" = d.crate;
        "${n}-zip" = d.zip;
      });
      apps.${system} = each (n: d: { ${n} = d.apps.default; "${n}-open" = d.apps.open; });
      devShells.${system} = nixpkgs.lib.mapAttrs (_: d: d.shell) documents;
    };
}
