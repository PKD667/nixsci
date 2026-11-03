# Typesetting: the TeX Live environment of a document, the command that builds it, and the PDF with
# its receipt.
#
#   latex { name; template; projects; packages; entry; sourceTree; unclean; marked; }
#     -> { flag; texEnv; texNames; command; stem; pdf; }
#   texEnv    texliveSmall with the packages the template, the document and its projects name
#   texNames  those packages by TeX Live name, sorted (the README lists them)
#   command   the latexmk invocation; the zip's README prints it and zipCheck runs it
#   pdf       $out/<name>.pdf and $out/receipt.json (the command, the TeX environment, what was untraced)
#   marked    the PDF must carry nixsci's DRAFT or REMEASURED mark: a build whose TeX never loaded nixsci fails
{ pkgs, lib, engines, pkgNames }:
{ name, template, projects, packages, entry, sourceTree, unclean, marked }:
let
  t = template;
  flag = engines.flag t.engine;

  # texliveSmall has no latexmk binary, and the README's command runs it.
  texPkgs = ps: t.packages ps ++ pkgNames packages ps ++ lib.concatMap (p: p.packages ps) projects ++ [ ps.latexmk ];
  texEnv = engines.texEnv texPkgs;
  # A TeX Live package's name: an entry of the texlive set carries it in its `pkgs` list.
  texName = p: p.pname or (lib.head p.pkgs).pname;
  texNames = lib.sort (a: b: a < b) (lib.unique (map texName (texPkgs pkgs.texlive)));

  # `-r nixsci/latexmkrc` (a toolkit file) fixes the PDF's date and gives a font engine a HOME, so the
  # README's command needs no environment set by hand.
  command = [ "latexmk" flag "-r" "nixsci/latexmkrc" "-interaction=nonstopmode" "-file-line-error" "-halt-on-error" entry ];
  stem = lib.removeSuffix ".tex" entry;

  # FORCE_SOURCE_DATE: the PDF carries the sandbox's fixed date, not the build time. The receipt
  # is written after latexmk returned, so it records a compile that happened.
  pdf = pkgs.runCommand "${name}-pdf" { nativeBuildInputs = [ texEnv pkgs.jq ]; FORCE_SOURCE_DATE = "1"; } ''
    ${engines.home t.engine}
    cp -RL --no-preserve=mode ${sourceTree}/. .
    ${lib.escapeShellArgs command}
    ${lib.optionalString marked ''
      # The marks are hooks nixsci.sty installs. A document that never loaded it would ship unmarked.
      grep -q 'nixsci/nixsci\.sty$' ${stem}.fls || {
        echo "doc: ${name}: ${entry} does not load nixsci, so this PDF would not show its DRAFT or REMEASURED mark; add \usepackage{nixsci}" >&2
        exit 1
      }
    ''}
    mkdir -p $out
    cp ${stem}.pdf $out/${name}.pdf
    jq -n --argjson command ${lib.escapeShellArg (builtins.toJSON command)} \
      --argjson unclean ${lib.escapeShellArg (builtins.toJSON unclean)} \
      --arg tex ${texEnv} --arg version "$(latexmk -v | head -n 2 | tail -n 1)" \
      '{command: $command, exitStatus: 0, texEnvironment: $tex, latexmk: $version, unclean: $unclean}' > $out/receipt.json
  '';
in
{ inherit flag texEnv texNames command stem pdf; }
