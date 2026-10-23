# Fonts for nixsci documents and figures. A font is declared once, here, and rendered into
# nixsci/fonts.tex (one \nixsci@font line per role, read by nixsci-fonts.sty) and into the list of
# files the tree must contain. See doc/INTERFACE.md, "doc.font".
#
#   fonts = import ./fonts.nix { inherit lib; };
#   fonts.mk     { family, dir?, tex?, ext?, faces?, fake? } -> normalised font, or an error
#   fonts.fontsTex  roles -> text of nixsci/fonts.tex
#   fonts.files     roles -> [ { to; from; } ] for the dir fonts
#   fonts.texPackages roles -> [ "name" ] TeX Live packages of the tex fonts
#
# `roles` is an attrset role -> font, where a font is either a result of mk or a plain attrset
# that mk accepts. mk is idempotent: mk (mk f) == mk f.
{ lib }:
let
  faceKeys = [ "regular" "bold" "italic" "bolditalic" ];
  fakeKeys = [ "bold" "slant" ];
  fontKeys = [ "family" "dir" "tex" "ext" "faces" "fake" ];

  unknownIn = known: attrs: lib.subtractLists known (lib.attrNames attrs);

  # True for a derivation output ("${pkgs.x}/share/fonts"): reading it builds the derivation at
  # evaluation time, so mk leaves its files unchecked. Plain paths and flake inputs are sources
  # (context { path = true; }) and are checked.
  isBuiltOutput = dir: lib.any (c: c ? outputs) (lib.attrValues (builtins.getContext (toString dir)));

  # A family name becomes a TeX argument and a directory name, so it stays within both.
  badFamily = family:
    family == "" || lib.any (c: lib.hasInfix c family) [ "{" "}" "\\" "%" "#" "/" "\n" ];

  isNumber = v: builtins.isInt v || builtins.isFloat v;

  # fontspec's file stem for a face that is not named.
  defaultStem = family: k:
    {
      regular = family;
      bold = "${family}-Bold";
      italic = "${family}-Italic";
      bolditalic = "${family}-BoldItalic";
    }.${k};

  # The files of a dir font that fontspec reads, as names in the directory. A named face is its
  # stem with ext. An unnamed face is fontspec's default stem, when the directory holds that file.
  # The regular face is always needed; a named file that is missing is an error.
  shipped = f: dirFiles:
    let
      stem = k: if f.faces.${k} != "" then f.faces.${k} else defaultStem f.family k;
      file = k: "${stem k}${f.ext}";
      isFile = n: lib.elem (dirFiles.${n} or "") [ "regular" "symlink" ];
      missing = lib.filter (k: f.faces.${k} != "" && !(isFile (file k))) faceKeys;
    in
    if missing != [ ] then
      throw "doc.font ${f.family}: no file ${lib.concatMapStringsSep ", " file missing} in ${toString f.dir}"
    else if !(isFile (file "regular")) then
      throw "doc.font ${f.family}: no file ${file "regular"} in ${toString f.dir}"
    else lib.unique (map file (lib.filter (k: f.faces.${k} != "" || isFile (file k)) faceKeys));

  mk = f0:
    let
      f = f0 // lib.optionalAttrs (builtins.isAttrs (f0.dir or null) && f0.dir ? outPath) { dir = f0.dir.outPath; };
      family =
        if f ? family && builtins.isString f.family then f.family
        else throw "doc.font: family is required and must be a string";
      err = msg: throw "doc.font ${family}: ${msg}";
      unknown = unknownIn fontKeys f;
      faces = f.faces or { };
      fake = f.fake or { };
      ext = f.ext or ".ttf";
      hasDir = (f.dir or null) != null;
      hasTex = (f.tex or null) != null;
      faceValue = k: v:
        if !(builtins.isString v) then err "face ${k} must be a file stem (a string), not ${builtins.typeOf v}"
        else if lib.any (c: lib.hasInfix c v) [ "{" "}" "\\" "%" "#" "/" "\n" ] then err "face ${k} = ${v} has a character TeX or a path cannot take"
        else v;
      badFake = lib.findFirst (k: fake ? ${k} && fake.${k} != null && !isNumber fake.${k}) null fakeKeys;
      checkDir = hasDir && !isBuiltOutput f.dir;
      dirFiles = if checkDir then builtins.readDir f.dir else { };
    in
    if unknown != [ ] then err "unknown key(s) ${lib.concatStringsSep ", " unknown} (known: ${lib.concatStringsSep ", " fontKeys})"
    else if badFamily family then err "family must be a non-empty name with no braces, backslash, %, #, / or newline"
    else if hasDir == hasTex then err "exactly one of dir and tex is required (got ${if hasDir then "both" else "neither"})"
    else if !(builtins.isString ext) then err "ext must be a string, like \".ttf\""
    else if !(builtins.isAttrs faces) then err "faces must be an attrset"
    else if lib.subtractLists faceKeys (lib.attrNames faces) != [ ] then
      err "unknown face key(s) ${lib.concatStringsSep ", " (unknownIn faceKeys faces)} (known: ${lib.concatStringsSep ", " faceKeys})"
    else if !(builtins.isAttrs fake) then err "fake must be an attrset"
    else if unknownIn fakeKeys fake != [ ] then
      err "unknown fake key(s) ${lib.concatStringsSep ", " (unknownIn fakeKeys fake)} (known: ${lib.concatStringsSep ", " fakeKeys})"
    else if badFake != null then err "fake.${badFake} must be a number, not ${builtins.typeOf fake.${badFake}}"
    else if hasDir && !(builtins.isString f.dir || builtins.isPath f.dir) then err "dir must be a path or a store path"
    else if checkDir && !(builtins.pathExists f.dir && builtins.readFileType f.dir == "directory")
      then err "dir ${toString f.dir} is not a directory"
    else if hasTex && !(builtins.isString f.tex) then err "tex must be a TeX Live package name"
    else
      let
        font = {
          inherit family ext;
          dir = if hasDir then f.dir else null;
          tex = if hasTex then f.tex else null;
          faces = lib.genAttrs faceKeys (k: if faces ? ${k} then faceValue k faces.${k} else "");
          fake = { bold = fake.bold or null; slant = fake.slant or null; };
        };
      in
      if checkDir then builtins.seq (shipped font dirFiles) font else font;

  # The font's TeX arguments, as fontsTex writes them.
  fontPath = f: if f.dir != null then "nixsci/fonts/${f.family}/" else "";

  sortedRoles = roles: map (r: { role = r; font = mk roles.${r}; }) (lib.sort (a: b: a < b) (lib.attrNames roles));

  # fake values are fontspec numbers, written as they are.
  num = v: if v == null then "" else builtins.toJSON v;

  line = r:
    let f = r.font; in
    "\\nixsci@font{${r.role}}{${f.family}}{${fontPath f}}{${f.ext}}"
      + lib.concatMapStrings (k: "{${f.faces.${k}}}") faceKeys
      + "{${num f.fake.bold}}{${num f.fake.slant}}";
in
{
  inherit mk;

  fontsTex = roles: lib.concatMapStrings (r: line r + "\n") (sortedRoles roles);

  # Every file of every dir font, once. Two roles that name the same family must give the same
  # file for each name, or the tree would hold two different files under one name.
  files = roles:
    let
      entries = lib.concatMap (r:
        let f = r.font; in
        if f.dir == null then [ ]
        else map (n: { to = "nixsci/fonts/${f.family}/${n}"; from = "${f.dir}/${n}"; })
          (shipped f (builtins.readDir f.dir))) (sortedRoles roles);
      add = acc: e:
        if acc ? ${e.to} && toString acc.${e.to}.from != toString e.from
        then throw "doc.font: ${e.to} is provided twice, with different files"
        else acc // { ${e.to} = e; };
    in
    lib.attrValues (lib.foldl' add { } entries);

  texPackages = roles:
    lib.unique (lib.sort (a: b: a < b)
      (lib.concatMap (r: if r.font.tex == null then [ ] else [ r.font.tex ]) (sortedRoles roles)));
}
