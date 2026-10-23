# Figures: how a document names the figures of a project, how wide each is, and what parameters
# its source reads. Pure functions of Nix values; doc.nix calls them.
#
#   figs = import ./figures.nix { inherit lib; };
#   figs.figuresIn ./figs                   -> { <stem> = <path>; ... } for every *.tex in the directory
#   figs.width where base w                 -> a TeX length string (w: a string, or a fraction of base)
#   figs.paramsTeX where params             -> TeX that defines \figparam and the parameters
{ lib }:
let
  # Digits to an int without lib.toInt: fromJSON rejects leading zeros ("0500").
  digitsToInt = s: lib.foldl' (acc: c: acc * 10 + lib.toInt c) 0 (lib.stringToCharacters s);

  # "398.3386pt" -> { int = 398; frac = "3386"; unit = "pt"; }; null if not a number and unit.
  lengthOf = s:
    let m = builtins.match "([0-9]+)(\\.([0-9]*))?([a-z]+)" s;
    in if m == null then null else {
      int = digitsToInt (lib.head m);
      frac = lib.defaultTo "" (builtins.elemAt m 2);
      unit = lib.last m;
    };

  # Four decimals, always: 199.1693pt, 12.0000cm. The number is rounded to the nearest 1e-4.
  fourDecimals = x:
    let
      r = builtins.floor (x * 10000 + 0.5);
      whole = r / 10000;
    in "${toString whole}.${lib.fixedWidthString 4 "0" (toString (r - whole * 10000))}";

  # width: a TeX length string, or a number in (0, 1] of `base` (the template's figure.width).
  width = where: base: w:
    if lib.isString w then w
    else if lib.isInt w || lib.isFloat w then
      if w > 0 && w <= 1 then
        let l = lengthOf base;
        in
        if l == null then throw "doc: ${where}: the template's figure.width is `${base}`, which is not a length (number and unit), so it has no fraction"
        else
          let
            digits = lib.stringLength l.frac;
            fracVal = if digits == 0 then 0.0 else (digitsToInt l.frac * 1.0) / (digitsToInt "1${lib.fixedWidthString digits "0" ""}" * 1.0);
          in "${fourDecimals ((l.int + fracVal) * w)}${l.unit}"
      else throw "doc: ${where}: width ${toString w} is a fraction of the figure width, and must be in (0, 1]"
    else throw "doc: ${where}: width must be a TeX length string or a number in (0, 1]";

  # params: attrset of strings, ints and bools. A bool is 1 or 0. The names become \figparam keys.
  paramValue = where: n: v:
    if lib.isBool v then (if v then "1" else "0")
    else if lib.isInt v then toString v
    else if lib.isString v then
      if lib.hasInfix "#" v then throw "doc: ${where}: parameter ${n} contains #, which TeX cannot take in a value"
      else v
    else throw "doc: ${where}: parameter ${n} must be a string, an int or a bool";

  paramsTeX = where: params:
    let
      names = lib.attrNames params;
      bad = lib.filter (n: builtins.match "[A-Za-z0-9_-]+" n == null) names;
      defs = map (n: "\\expandafter\\def\\csname nixsci@param@${n}\\endcsname{${paramValue where n params.${n}}}")
        (if bad == [ ] then names else throw "doc: ${where}: parameter names are letters, digits, - and _: ${lib.concatStringsSep ", " bad}");
    in lib.concatStringsSep "\n" ([
      # The wrapper does not load nixsci.sty, so the names with @ are read with \makeatletter.
      "\\makeatletter"
      # \figparam{name} reads a parameter; \figparam[default]{name} falls back to the default. A missing
      # parameter with no default is an error, never a blank. The sentinel is \relax, so \ifx matches
      # whether or not the optional default was expanded when the macro was defined.
      "\\let\\nixsci@nodefault\\relax"
      "\\NewExpandableDocumentCommand{\\figparam}{O{\\nixsci@nodefault} m}{%"
      "  \\ifcsname nixsci@param@#2\\endcsname\\csname nixsci@param@#2\\endcsname"
      "  \\else\\ifx#1\\nixsci@nodefault"
      "    \\PackageError{nixsci-figure}{no figure parameter `#2'}{The document's figures entry sets no such parameter, and the figure gives no default.}%"
      "  \\else#1\\fi\\fi}"
      "\\makeatother"
    ] ++ defs);

  # figuresIn dir: every *.tex file of dir, key = stem. Other files are ignored.
  figuresIn = dir:
    let
      entries = builtins.readDir dir;
      texNames = lib.filter (n: lib.elem entries.${n} [ "regular" "symlink" ] && lib.hasSuffix ".tex" n)
        (lib.attrNames entries);
    in lib.listToAttrs (map (n: lib.nameValuePair (lib.removeSuffix ".tex" n) (dir + "/${n}")) texNames);
in
{
  inherit figuresIn width paramsTeX;
}
