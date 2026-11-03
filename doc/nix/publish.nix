# What a document ships: the RO-Crate, the LaTeX zip (and the check that it compiles alone), and the
# `publish` directory with the PDF, the zip, the reproduction artifact and SHA256SUMS. `publish` is
# refused at build time when the document is a draft or its flake has uncommitted changes.
#
#   publish { name; template; source; projects; unclean; tree; sourceTree; fontFiles; latex; }
#     -> { crate; zip; zipCheck; refusals; publish; }
{ pkgs, lib, crateTool, engines }:
{ name, template, source, projects, unclean, tree, sourceTree, fontFiles, latex }:
let
  t = template;
  inherit (latex) flag texEnv texNames command stem;
  pdfBase = latex.pdf;

  crate = pkgs.runCommand "${name}-ro-crate" { nativeBuildInputs = [ crateTool ]; } ''
    python ${../crate.py} --name ${lib.escapeShellArg name} --source ${sourceTree} \
      --pdf ${pdfBase}/${name}.pdf --receipt ${pdfBase}/receipt.json --includes ${pkgs.writeText "none.json" "{}"} \
      --origins ${tree}/origins.tsv --out $out
  '';

  dirty = source != null && !(source ? rev);
  refusals = unclean
    ++ lib.optional (source == null) "the document does not name its flake (source = self), so its tree cannot be checked"
    ++ lib.optional dirty "the flake has uncommitted changes";
  # Refused at build time, not evaluation: the flake still evaluates while you write.
  publish =
    if refusals == [ ] then publishDir
    else pkgs.runCommand "${name}-publish-refused" { } ''
      echo ${lib.escapeShellArg "doc: ${name} cannot be published:\n  ${lib.concatStringsSep "\n  " refusals}"} >&2
      exit 1
    '';

  # zipCheck runs the command line this prints.
  engineName = engines.name t.engine;
  readme = pkgs.writeText "${name}-README.txt" (lib.concatStringsSep "\n" ([
    "${name}-latex.zip: the LaTeX source of ${name}, with its figures and style files."
    "Unzip it into an empty directory, then run this in that directory:"
    ""
    "    ${lib.escapeShellArgs command}"
    ""
    "That writes ${stem}.pdf. ${if texNames == [ ] then "It needs no TeX Live packages beyond the base install." else "It needs these TeX Live packages (texliveSmall; the packages they depend on come with them): ${lib.concatStringsSep ", " texNames}."}"
    ""
    "For the same bytes as the published PDF, set FORCE_SOURCE_DATE=1 and SOURCE_DATE_EPOCH=315532800 first."
  ] ++ lib.optionals (engineName != null) [
    "The engine is ${engineName}: the command above passes ${flag}, so run it exactly as written."
    "Overleaf: Menu > Compiler, choose ${engineName}. ${engineName} writes a font cache: set HOME to a writable directory first."
  ] ++ lib.optional (fontFiles != [ ]) "The fonts under nixsci/fonts keep their own licence.") + "\n");

  # The LaTeX project: the source tree and README.txt. Sorted names and fixed times, so two
  # builds give the same bytes.
  zip = pkgs.runCommand "${name}-zip" { nativeBuildInputs = [ pkgs.zip ]; } ''
    mkdir -p $out work
    cp -RL --no-preserve=mode ${sourceTree}/. work/
    [ ! -e work/README.txt ] || { echo "doc: ${name} has a README.txt at its root; the LaTeX zip adds one" >&2; exit 1; }
    cp ${readme} work/README.txt
    cd work
    find . -exec touch -h -d @0 {} +
    find . -type f -printf '%P\n' | LC_ALL=C sort | zip -X -D -q $out/${name}.zip -@
  '';

  zipCheck = pkgs.runCommand "${name}-zip-check"
    { nativeBuildInputs = [ pkgs.unzip pkgs.gnugrep ]; FORCE_SOURCE_DATE = "1"; } ''
    unzip -q ${zip}/${name}.zip
    line=$(grep -m1 '^    latexmk ' README.txt) || { echo "doc: ${name}: README.txt prints no latexmk command" >&2; exit 1; }
    ${pkgs.coreutils}/bin/env -i PATH=${texEnv}/bin:${pkgs.coreutils}/bin:${pkgs.bash}/bin \
      TEXMFHOME=/nonexistent HOME=$TMPDIR FORCE_SOURCE_DATE=1 SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH \
      sh -c "$line" > build.out 2>&1 || { cat build.out; echo "doc: ${name}: the README's command failed" >&2; exit 1; }
    if grep -nE "There were undefined references|Citation .* undefined|Reference .* undefined" ${stem}.log; then
      echo "doc: ${name}: the README\'s command leaves an undefined reference" >&2; exit 1
    fi
    mkdir $out
    cp ${stem}.pdf $out/${name}.pdf
  '';

  analysisViews = lib.concatMap (p: lib.mapAttrsToList (key: view: { project = p.name; inherit key view; }) p.analyses) projects;

  # publish: the document, its LaTeX project, and the reproduction artifact, with SHA256SUMS.
  publishDir = pkgs.runCommand "${name}-publish" { nativeBuildInputs = [ pkgs.zip ]; } ''
    # The zip is only shipped once it has compiled alone with its README's command (zipCheck).
    test -f ${zipCheck}/${name}.pdf
    mkdir -p $out art
    cp ${pdfBase}/${name}.pdf $out/${name}.pdf
    cp ${zip}/${name}.zip $out/${name}-latex.zip
    cp -RL --no-preserve=mode ${crate}/. art/
    cat > art/ARTIFACT-README.txt <<'EOF'
    This archive contains the RO-Crate files and the analysis views listed under data/.
    These views are not full raw experiment run directories.
    EOF
    ${lib.concatMapStrings (a: ''
      mkdir -p ${lib.escapeShellArg "art/data/${a.project}/${a.key}/view"}
      cp -RL --no-preserve=mode ${a.view}/. ${lib.escapeShellArg "art/data/${a.project}/${a.key}/view"}/
    '') analysisViews}
    find art -exec touch -h -d @0 {} +
    (cd art && find . -type f -printf '%P\n' | LC_ALL=C sort | zip -X -D -q $out/${name}-artifact.zip -@)
    cd $out
    files=$(find . -maxdepth 1 -type f -printf '%f\n' | LC_ALL=C sort)
    sha256sum $files > SHA256SUMS
  '';
in
{ inherit crate zip zipCheck refusals publish; }
