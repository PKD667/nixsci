# nixsci out

`nixsci.lib.out { inherit pkgs; }` returns `project`, `template`, `templates`, `document`, `static` and `output`. The full signatures are in `nix/out.nix`.

```nix
out = nixsci.lib.out { inherit pkgs; };

p = out.project {
  name = "p";
  style = ./p.sty;                                   # \usepackage{p}
  packages = ps: [ ps.pgfplots ];                    # TeX packages the style and figures need
  files.assets = ./assets;                           # p/assets/...
  data.band = out.static ./data/band.tsv;            # read by figures; static = untraced
  figures.band = { src = ./figures/band.tex; data = [ "band" ]; };   # p/band.pdf
  values.band = out.output analysis "band.json";     # \val{p/band.median}
  bib = ./references.bib;                            # \bibliography{p}
};

doc = out.document {
  name = "doc";
  src = ./doc;                     # holds main.tex, which names its class
  template = out.templates.plain;  # or a template from out.template
  meta = { };                      # checked against the template's slots
  projects = [ p ];
  figures.p.band = "8.6cm";        # width for this document; { from; width; options; } adds a variant
  source = self;                   # lets publish check for uncommitted changes
};
# doc.pdf  doc.publish  doc.refusals  doc.source  doc.crate  doc.zip  doc.shell  doc.apps  doc.unclean
```

`doc.publish` builds a directory containing `doc.pdf`, `doc-latex.zip`, `doc-artifact.zip`, and `SHA256SUMS`. The LaTeX archive includes the dereferenced source tree, compiled figures, and a README with its compile command and TeX Live package requirements. The artifact archive contains the RO-Crate plus analysis views available through project outputs; these views are not full raw experiment runs. The checksum file covers the three sibling deliverables. No upload occurs.

## Figures

Each figure is compiled in its own build, with the `standalone` class, the project's style and the template's `figure` settings (`width`, `size`, `preamble`, `packages`). In that build, `\datadir` holds the declared data as `<key>.<ext>`, `\figwidth` is the requested width, and the project's files are at `p/<key>`. The PDF is placed at `p/<figure>.pdf` in the document's tree.

## Values

Each `values` entry is a JSON file. Its scalars become `\val{p/<entry>.<path>}`, where `<path>` is the JSON path joined with dots. `\val` with an unknown key is a LaTeX error.

## Templates

`out.template { name; files; packages; engine; meta; render; figure; }`. `meta` maps each slot to `"required"` or `"optional"`; `meta = null` accepts any slots. `render` turns the document's `meta` into TeX, which `nixsci.sty` reads when it loads. A template's class must load `nixsci` after defining what `render` sets. `out.templates` has `plain`, `paper`, `slides` and `poster`.

## Provenance

`unclean` lists every input marked `static`, and every lab analysis that is not clean. If the list is not empty, each page of the PDF gets a DRAFT line. `crate` is the RO-Crate; `publish` is the four-file directory above. Its build fails with `refusals` when `unclean` is not empty, when `source` is missing, or when the flake has uncommitted changes. `zip` remains the LaTeX source archive.

## Editing

`nix run .#<doc>` links the assembled tree (template files, `nixsci.sty`, projects, compiled figures) to `.nixsci/out/<doc>`, puts it on `TEXINPUTS`, `BIBINPUTS` and `BSTINPUTS`, exports `NIXSCI_TREE`, and runs `latexmk -pvc` in `srcDir`. After editing a figure source, run `nix run` again.

`nixsci out <dir>` prints prose warnings (WRITING.md budgets and deny-list). It always exits 0.

## Remeasure

An exported PDF package exposes `measurementPlan` and `withMeasurements`. Run `nixsci out remeasure FLAKE#DOC --level 0.95 --output DIR` to execute every job in its original experiment specs with the original closures, in isolated empty stores, and build a fresh PDF from those locks. The output directory must be empty; failed runs retain diagnostics but cannot leave a stale PDF looking successful. `reports/` contains each claim comparison and `summary.json`; the PDF marks every page with the contradicted, inconclusive and unsupported counts. Confidence is a two-sided interval at the requested per-claim level, not a joint guarantee. No machine-hardware comparison is made. `formalVerification: not-performed` in statistical reports means provenance checks are not proofs.

The remeasurement path changes only lock inputs and analysis outputs. It does not override the code flake, make temporary commits, or modify the source checkout.
