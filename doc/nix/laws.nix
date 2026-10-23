# Properties of what a document ships, checked over every document given:
#   roundtrip  the zip, built with its README's command, gives the same PDF bytes as the document
#   layout     a zip entry is the user's, README.txt, nixsci.cls, nixsci.sty, or under nixsci/
#   plain      no zip entry mentions directlua, getenv or NIXSCI_TREE
# docs: [ { name; pdf; zip; zipCheck; src; } ]
{ pkgs }: docs:
let
  check = d: ''
    echo "== ${d.name}"
    cmp ${d.pdf}/${d.name}.pdf ${d.zipCheck}/${d.name}.pdf \
      || { echo "roundtrip: ${d.name}: the zip does not rebuild the same PDF" >&2; exit 1; }

    unzip -Z1 ${d.zip}/${d.name}.zip | LC_ALL=C sort > entries
    (cd ${d.src} && find . -type f | sed 's|^\./||' | LC_ALL=C sort) > user
    while read -r e; do
      case "$e" in
        README.txt|nixsci.sty|nixsci.cls|nixsci/*) ;;
        *) grep -qxF "$e" user || { echo "layout: ${d.name}: $e" >&2; exit 1; } ;;
      esac
    done < entries

    rm -rf unzipped && mkdir unzipped && (cd unzipped && unzip -q ${d.zip}/${d.name}.zip)
    if grep -rEl 'directlua|getenv|NIXSCI_TREE' unzipped; then
      echo "plain: ${d.name}: a zip entry mentions Lua or the environment" >&2; exit 1
    fi
  '';
in
pkgs.runCommand "nixsci-layout-laws" { nativeBuildInputs = [ pkgs.unzip pkgs.gnugrep pkgs.gnused pkgs.coreutils ]; } ''
  ${pkgs.lib.concatMapStrings check docs}
  mkdir $out
''
