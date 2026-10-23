# nixsci doc: the unified template interface

Status: proposal, implemented on branch `unified-template`. This file is the contract the
implementation is checked against. Where it and the code disagree, one of them is a bug.

## What is wrong now

1. A document repeats what the flake already declared: `\nixscistyle{slides}` and
   `\nixsciproject{aufbau}` name the template and project a second time.
2. Fonts are declared twice with no check that they agree: in the template's `figure.preamble`
   (Nix, as TeX strings) and in the deck's `slides.tex` (a `\directlua` block that reads
   `NIXSCI_TREE` to build a `\fontdir` path). Lua exists only in LuaLaTeX.
3. Generated files sit loose beside the user's: `aufbau.sty`, `aufbau.bib`, `aufbau/`, `lmpl26/`,
   `nixsci-generated.tex` at the root of the tree and the zip. `.nixsci/` directories appear in
   whatever directory a dev shell was started in.
4. The class and its packages are the template's business but sit in every source:
   `\documentclass[aspectratio=169,12pt]{beamer}`, `\usepackage{booktabs}`, `\usepackage{tikz}`,
   the deck's colours and `\setbeamertemplate` lines. The template already knows all of it.
5. Figure widths are raw points (`"378.4217pt"`), variants take TeX in a Nix string
   (`options = "\\def\\entropyonlymodel{1}"`), and every figure is listed by hand.

## The workspace: simple flakes

The flake of a papers repository is eight lines. nixsci finds the declarations by directory
convention and builds every output. `nixsci.lib.workspace { inherit inputs; root = ./.; }` returns
`packages`, `apps`, `devShells` and `checks` (the flake's `system` defaults to x86_64-linux).

```nix
{
  inputs = { nixsci.url = "git+file:///home/pkd/code/nixsci"; nixpkgs.follows = "nixsci/nixpkgs";
             fontdir = { url = "path:/home/pkd/fonts/berkeley"; flake = false; }; };
  outputs = inputs: inputs.nixsci.lib.workspace { inherit inputs; root = ./.; };
}
```

### Discovery
`workspace` walks `root` (skipping `archive/`, `result*`, dot directories, `node_modules`) and reads:

| file | declares | name |
|---|---|---|
| `project.nix` | a project | `name` or the directory's name |
| `document.nix` | a document | `name` or the last two path components joined by `-` (`aufbau/lmpl26/paper` -> `lmpl26-paper`, `nerve/report` -> `nerve-report`, `cv/cs` -> `cv-cs`) |
| `template.nix` | a template | `name` or the directory's name |

A file holds a plain attrset, or a function `{ pkgs, inputs, doc, workspace, ... }: attrset` when it
needs code (a derivation, a flake input). Every other attribute is data. Unknown keys are an
evaluation error naming the file and the key; the schema is the list below and nothing else.
Names are strings: `template = "deck"`, `projects = [ "aufbau" ]`. An unknown name is an error that
lists the known ones. Built-in templates (`plain`, `paper`, `slides`, `poster`) are always known.

### Defaults that remove boilerplate
- `document.nix`: `src` defaults to the directory (a string is a subdirectory, `src = "tex"`),
  `entry` to `main.tex`, `srcDir` is computed from the path, `source` is the flake (publish checks
  work with no line from the user). A git flake has only tracked files, so there is no fileset.
- `project.nix`: `figures` is an attrset, or a directory (every `*.tex` in it is a figure); it defaults to the `figures/` next to the file, `packages`
  is a list of TeX Live names as strings (a function `ps: [...]` is still accepted), `bib` defaults to
  `references.bib` if present, `style` to `<name>.sty` if present, `files` to `assets/` if present.
- `template.nix`: `fonts`, `class`, `use`, `style` (a path next to the file), `engine`, `medium`,
  `meta`, `render`, `figure`, `packages`. Fonts are plain attrsets, `doc.font` is called for you; a
  flake input is `dir = inputs.fontdir`.
- Per document: `figures.<project>.<key>`, `meta`, `template`, `projects`, `packages`, `apps`
  (flake apps, a function of `{ pkgs, ... }`), `checks`.
- A project may return `passthru = { ... }`, exposed as `lib.<project name>` of the flake (nerve's
  figure-data bridge uses it).

### The shape this gives `~/papers`
```
flake.nix                                     8 lines
aufbau/project.nix  aufbau.sty  figures/*.tex  bib/  logos/
aufbau/lmpl26/paper/document.nix  main.tex
aufbau/lmpl26/slides/document.nix  tex/slides.tex       content only
aufbau/templates/deck/template.nix  deck.sty            colours, \claim, beamer templates
nerve/project.nix  report/document.nix  talk-ircica/document.nix
cv/template.nix  cv/cs/document.nix ...
```

## The source a user writes

```latex
\documentclass{nixsci}         % the template's class, options, packages, fonts and style file
\begin{document}
\maketitle                     % title, authors, DOI come from the flake's `meta`
{\displayfont A title}  \includegraphics{aufbau/worktree}  \val{nerve/band.median}
\nixscibib{aufbau}             % bibliography of a project
```

`\documentclass[handout]{nixsci}`: extra options are appended to the template's own. The slide
macros (`\claim`, colours, `\setbeamertemplate`) live in the template's style file, not in
`slides.tex`. No `\nixscistyle`, no `\nixsciproject`, no `\usepackage{fontspec}`, no `\directlua`,
no paths. Old documents keep working: `\usepackage{nixsci}` under a class the document names
itself is the `plain` template's form, and `\nixscistyle`/`\nixsciproject` stay, doing nothing new.

## The Nix a user writes (primitives)

In a workspace these are the attrsets of `template.nix`, `project.nix` and `document.nix`; the
functions below (`doc.font`, `doc.template`, `doc.figuresIn`) are what `workspace` calls, and they
stay public for a flake that does not use the workspace.

```nix
berkeley    = doc.font { family = "BerkeleyPatched"; dir = fontdir; ext = ".ttf";
                         fake = { bold = 1.4; slant = 0.15; }; };
baskerville = doc.font { family = "LibreBaskerville"; dir = "${pkgs.libre-baskerville}/share/fonts/truetype";
                         faces = { regular = "LibreBaskerville-Regular"; bold = "LibreBaskerville-Bold";
                                   italic = "LibreBaskerville-Italic"; bolditalic = "LibreBaskerville-Italic"; }; };
inconsolata = doc.font { family = "Inconsolata"; tex = "inconsolata"; ext = ".otf";
                         faces = { regular = "InconsolataN-Regular"; bold = "InconsolataN-Bold"; }; };
libertine   = doc.font { family = "LinLibertine"; tex = "libertine"; ext = ".otf";
                         faces = { regular = "LinLibertine_R"; italic = "LinLibertine_RI";
                                   bold = "LinLibertine_RB"; bolditalic = "LinLibertine_RBI"; }; };

deck = doc.template {
  name = "lmpl26-deck";  engine = "lualatex";
  fonts = { main = berkeley; display = baskerville; mono = inconsolata; };
  figure = { width = "398.3386pt"; size = "9pt"; fonts = { main = libertine; mono = inconsolata; }; };
};

aufbau = doc.project { name = "aufbau"; figures = doc.figuresIn ./lmpl26/paper/figs; ... };

slides = doc.document {
  ...
  figures.aufbau = {
    worktree = 0.95;                                              # a fraction of figure.width
    entropy-model = { from = "entropy"; params.only-model = true; };
  };
};
```

### `doc.font`
`{ family, dir ? null, tex ? null, ext ? ".ttf", faces ? {}, fake ? {} }`.
- Exactly one of `dir` (a store path or directory with the font files) and `tex` (a TeX Live
  package that ships them, found by kpathsea) is set; both or neither is an evaluation error.
- `faces` maps `regular|bold|italic|bolditalic` to file stems. Omitted: fontspec's own defaults
  (`<family>`, `<family>-Bold` ...) are used and none of the four options are written.
- `fake.bold`, `fake.slant`: fontspec's `FakeBold`, `FakeSlant`.
- A `dir` font puts in the zip the files its faces name: a named face's stem with `ext`, or for an
  unnamed face fontspec's default stem when the directory has that file. The regular face must exist.
  Every font in the zip ships; there is no option to leave one out.
- `tex = "x"` adds TeX Live package `x` to the document's and the figures' package lists.

### `doc.template`: class, packages, style
New optional arguments, all additive:
- `class ? null`: `{ name = "beamer"; options = [ "aspectratio=169" "12pt" ]; }`. Null means the
  document names its own class (the `plain` template).
- `use ? []`: packages to `\usepackage`, each a string or `{ name = options; }`:
  `[ "booktabs" "mathtools" { ulem = "normalem"; } "tikz" ]`. Shared by the document and its figures;
  `figure.use` adds figure-only ones. Each also lands in the TeX Live package list.
- `style ? null`: a `.sty` file of the template's own (the deck's colours, `\claim`, beamer
  templates). Loaded after the class, packages and fonts. It lands as `nixsci/<name>.sty`.
- `medium ? null`: the toolkit mechanism set, one of `slides`, `paper`, `poster`: what
  `\nixscistyle{...}` was. `templates.slides` sets `medium = "slides"`.
- `meta`/`render` as now, and every field a class needs is a meta slot: `title`, `authors`, `doi`.
  `render` turns the meta into `\title`, `\author`, `\acmDOI` and the like. The paper's
  `main.tex` stops declaring them.

### `template.fonts`
An attrset role -> font. Roles `main`, `sans`, `mono` call `\setmainfont`, `\setsansfont`,
`\setmonofont`. Any other role `r` defines `\<r>font` with `\newfontfamily`. A non-empty `fonts`
with `engine = "pdflatex"` is an evaluation error naming the template ("fonts need lualatex or
xelatex"). `figure.fonts` is the same for a figure's standalone wrapper, so a figure and its
document cannot disagree by accident: the one place that says they differ is `figure.fonts`.

### Figures
- `doc.figuresIn dir`: every `*.tex` in `dir` as a figure, key = file stem. Replaces the hand list.
- `figures.<project>.<key>` is a string (a TeX length, as before), a number in (0, 1] (a fraction
  of `figure.width`), or `{ width?, from?, params? }`. `params` is an attrset of strings, ints and
  bools (bool -> 1 or 0). An unknown key in `params` is not an error: the figure source decides.
  `options` (TeX in a string) is removed; use `params`.
- In a figure source, `\figparam{name}` reads a parameter; `\figparam[default]{name}` falls back.
  A missing parameter without a default is a LaTeX error, never a blank.

## Layout

One directory holds everything nixsci generates or assembles:

| where | name | holds |
|---|---|---|
| checkout, build tree | `.nixsci/` in a checkout; `nixsci/` in the build tree and the zip | see below |

```
<tree root>               what the user wrote, unchanged
  main.tex  errors.tex ...
  nixsci.cls  nixsci.sty  3-line shims: set \input@path and \graphicspath, load nixsci/nixsci.cls|sty
  nixsci/                 everything else
    nixsci.cls  nixsci.sty  generated.tex  <template style>.sty
    <project>.sty  <project>.bib  <project>/<key>.<ext>     files and compiled figures
    fonts/<family>/<files>
```
- Build, store tree and zip have the same layout: the zip is the tree plus `README.txt`.
- A checkout holds generated state only in `<flake root>/.nixsci/` (flake root = `git rev-parse
  --show-toplevel`, else `$PWD`). The dev shell and `nix run` never create `.nixsci/` anywhere else.
- The root of the zip has nothing from nixsci except `nixsci.cls`, `nixsci.sty`, `nixsci/` and `README.txt`.
- `\nixsciroot` is the prefix of `nixsci/`: empty in the build and the zip (cwd is the tree root),
  an absolute path in the dev loop, passed on the latexmk command line
  (`-usepretex='\def\nixsciroot{...}'`). `nixsci.sty` uses `\providecommand`, so it is empty when unset.
  This replaces `NIXSCI_TREE` and every `os.getenv`.
- `TEXINPUTS`, `BIBINPUTS`, `TTFONTS`, `OPENTYPEFONTS` are no longer needed for fonts and figures.
  Keep them only where a test shows something still needs them, and say which.
- Whether Overleaf keeps and searches a directory named `nixsci/` is not verified here. The layout
  uses only `\input@path`, `\graphicspath` and relative `Path=`, which Overleaf documents. No dot
  directory is placed in the zip, because Overleaf's handling of those is unknown.

## The generated file, `nixsci/generated.tex`

This is the seam between the Nix side and `nixsci.sty`. Exactly these macros, one per line,
braces always present (empty argument = `{}`):

```
\nixsci@class{beamer}{aspectratio=169,12pt}     the template's class and options (empty = none)
\nixsci@medium{slides}                         selects \nixsci@style@slides
\nixsci@use{ulem}{normalem}                    one per package; second argument = options
\nixsci@style{deck}                            the template's own nixsci/deck.sty
\nixsci@project{aufbau}                        one per project with a style; loads <project>.sty
\nixsci@bib{aufbau}                            one per project with a bib
\nixsci@defval{project/key.path}{value}        as now
\nixsci@draft{n}   \nixsci@remeasure{text}     as now
```

Fonts live in their own file, `nixsci/fonts.tex`, one line per role, so the document and its figures
share one implementation (`nixsci-fonts.sty`):

```
\nixsci@font{role}{family}{path}{ext}{regular}{bold}{italic}{bolditalic}{fakebold}{fakeslant}
```
`nixsci-fonts.sty` reads `\nixsci@font` lines from `nixsci/fonts.tex` (the document) or `fonts.tex` in
the figure's build directory (the wrapper), loads `fontspec`, and applies them. `path` is relative to the tree root (`nixsci/fonts/<family>/`), or empty for a `tex` font. An
empty face or fake argument means "not set". It turns each `\nixsci@font` into one
`\setmainfont`, `\setsansfont`, `\setmonofont` or `\newfontfamily` call, with `Path=\nixsciroot<path>`.
The figure wrapper (a standalone document) loads `nixsci-fonts` with its own `fonts.tex` built
from `figure.fonts`, so no font is written twice, in Nix or in TeX.

`nixsci.cls` reads `generated.tex`, passes the document's options plus the template's to
`\LoadClass{<class>}`, then does what `nixsci.sty` does. `\usepackage{nixsci}` (a document that
names its own class) does the same minus `\LoadClass`. Order: class, `\nixsci@use` packages,
`fontspec` if any font is declared, the font macros, `\nixsci@style@<medium>`, each
`\nixsci@project`, then `\nixsci@style` (the template's style may use the projects' colours).
`\nixscibib{name}` expands to `\addbibresource{\nixsciroot nixsci/name.bib}` if biblatex is loaded,
else `\bibliography{\nixsciroot nixsci/name}`.

## Acceptance tests

Every item is a command that must pass. Say which you ran.

1. Old documents unchanged: `nix build` the three template documents under `doc/examples` and
   `doc/templates` from the base commit and from the branch; PDFs have the same page count.
   (`pdftotext` equal is enough; bytes differ by design.)
2. Zip compiles alone: unzip `<doc>-latex.zip` into an empty directory; run the README command with
   `env -i`, `TEXMFHOME=/nonexistent`, a `texliveSmall` built only from the README's package list.
   Exit 0, no `Warning.*undefined`. For every document, as a Nix check, not a one-off.
3. No Lua, no env: `grep -rn 'directlua\|getenv\|NIXSCI_TREE'` over `doc/` and a migrated document
   finds nothing.
4. Fonts: a lualatex fixture with a `dir` font and a `tex` font builds; `pdffonts` lists both. A
   template with `fonts` and `engine = "pdflatex"` fails evaluation with the message above. A font
   with both or neither of `dir` and `tex` fails evaluation.
5. Dev loop: starting `nix run` from a subdirectory creates no `.nixsci/` there.
6. The README of the zip names the TeX Live packages, including latexmk, and names the engine
   (`latexmk -lualatex ...`) so an Overleaf user can set the compiler.
7. A document with `class` declared builds from `\documentclass{nixsci}` and gives the same page
   count and `pdftotext` as the same document with the class and packages written out by hand.
8. `meta` is checked as before: a missing required slot fails evaluation, naming the slot.

## Phase 2, specified here and not yet built: the flat export

`<doc>-latex.zip` holds the abstract source (`\documentclass{nixsci}`) and works where
`nixsci/` is found. A journal portal, arXiv or ACM camera-ready needs plain LaTeX. `<doc>-flat.zip`
rewrites the first `\documentclass{nixsci}` of the entry file into the explicit
`\documentclass[opts]{class}`, the `\usepackage` list, the font calls and the rendered meta,
inlined from the same declarations, and keeps every other line. Test: the flat zip compiles under
acceptance test 2 and its PDF has the same page count as the abstract one.

## Acceptance tests for the workspace

9. A toy workspace under `doc/examples/workspace/` (two projects, a template with a font and a
    class, two documents, one using a figure of the other project) builds with a flake of eight
    lines. Its `nix flake show` lists every document's pdf, -publish, -source, -crate, -zip, an
    app and a dev shell, and a zip check.
10. An unknown key in a `document.nix`, an unknown template name and an unknown project name each
    fail evaluation with a message naming the file or the known names.
11. `workspace` equals the hand-written flake: for the toy workspace written both ways, the
    PDFs have equal `pdftotext`.
12. A directory called `archive/` is never read, even if it holds a `document.nix`.

## Split of the code (so that work does not collide)

`doc/nix/doc.nix` is 444 lines and does too much. New code goes in new files, and `doc.nix` only
imports them: `fonts.nix`, `figures.nix`, `template.nix`, `workspace.nix`. The tree, zip and dev
loop stay in `doc.nix` until a later split.
