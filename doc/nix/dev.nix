# The edit loop: the dev shell, and the apps `nix run .#<doc>` (latexmk -pvc on the checkout) and
# `.#<doc>-open` (the PDF). The loop edits the checkout: the tree (template, toolkit, projects, compiled
# figures) is symlinked under .nixsci/doc/<name> and put on the TeX search paths. A figure source edit
# needs `nix run` again, because the figure is a build.
#
#   dev { name; template; entry; src; srcDir; tree; latex; } -> { shell; apps; }
{ pkgs, lib, cliTool }:
{ name, template, entry, src, srcDir, tree, latex }:
let
  inherit (latex) flag texEnv stem;
  pdfBase = latex.pdf;

  sourceDir =
    if srcDir != null then srcDir
    else if builtins.typeOf src == "path" then baseNameOf (toString src)
    else null;
  # latexmk gets \nixsciroot from -usepretex: there is no environment hook for it.
  env = ''
    nixsci_root() {
      if r=$(git rev-parse --show-toplevel 2>/dev/null); then printf '%s\n' "$r"; return 0; fi
      d=$PWD
      while :; do
        if [ -f "$d/flake.nix" ]; then printf '%s\n' "$d"; return 0; fi
        [ "$d" = / ] && break
        d=$(dirname "$d")
      done
      echo "doc: no git checkout or flake.nix above $PWD" >&2
      return 1
    }
    root=$(nixsci_root) || exit 1
    state="$root/.nixsci"
    mkdir -p "$state/doc" "$state/bin"
    ln -sfn ${tree}/tree "$state/doc/${name}"
    nixsciroot="$state/doc/${name}/"
    cat > "$state/bin/latexmk" <<WRAP
#!/bin/sh
exec ${texEnv}/bin/latexmk '-usepretex=\def\nixsciroot{$nixsciroot}' "\$@"
WRAP
    chmod +x "$state/bin/latexmk"
    export PATH="$state/bin:$PATH"
    export TEXINPUTS="$state/doc/${name}:''${TEXINPUTS:-}"
  '';
  shell = pkgs.mkShell {
    packages = [ texEnv pkgs.xdg-utils pkgs.git pkgs.coreutils ];
    shellHook = env + lib.optionalString (sourceDir != null) ''echo "doc: cd ${sourceDir}; latexmk -pvc ${flag} ${entry}"'';
  };
  devApp = pkgs.writeShellApplication {
    name = "${name}-dev";
    runtimeInputs = [ texEnv pkgs.xdg-utils pkgs.git pkgs.coreutils ];
    text = lib.optionalString (cliTool != null) ''
      if [ "''${1:-}" = --remeasure ]; then
        shift
        exec ${cliTool}/bin/nixsci doc remeasure "$PWD"#${lib.escapeShellArg name} "$@"
      fi
    '' + env + (if sourceDir == null then ''
      echo "doc: ${name} needs srcDir for the dev loop: src is not a plain path" >&2
      exit 1
    '' else ''
      [ -d "$root/${sourceDir}" ] || { echo "doc: $root/${sourceDir} is not a directory: srcDir does not exist in this checkout" >&2; exit 1; }
      cd "$root/${sourceDir}"
      # -g: a stale latexmk state file can record an error and then refuse to recompile.
      latexmk -g ${flag} -interaction=nonstopmode -file-line-error -halt-on-error ${entry}
      xdg-open "${stem}.pdf" &
      exec latexmk -pvc ${flag} -interaction=nonstopmode -file-line-error -halt-on-error ${entry}
    '');
  };
  openApp = pkgs.writeShellApplication {
    name = "${name}-open";
    runtimeInputs = [ pkgs.xdg-utils ];
    text = "exec xdg-open ${pdfBase}/${name}.pdf";
  };
in
{
  inherit shell;
  apps = {
    default = { type = "app"; program = "${devApp}/bin/${name}-dev"; };
    open = { type = "app"; program = "${openApp}/bin/${name}-open"; };
  };
}
