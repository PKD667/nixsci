# A verdict is a derivation: a deterministic check's build is the check, and the Nix store records it.
# Reasons and the output format: validate/README.md.
#
#   v = nixsci.lib.validate { inherit pkgs; };
#   c = v.check { name = "rejections"; property = "..."; model = "..."; subject = ./x.py;
#                 checker = pkgs.python3; scope = { cases = 93; }; command = "..."; };
#   suite = v.suite { rejections = c; };     # { checks; verdicts; table; }
{ pkgs, python }:
let
  inherit (pkgs) lib;
  allowed = [ "name" "property" "model" "subject" "checker" "scope" "command" "runtime" ];
  scalar = x: builtins.isInt x || builtins.isString x || builtins.isBool x || builtins.isList x;

  check = c:
    let
      name = c.name or (throw "nixsci validate: a check needs a name");
      who = "check ${builtins.toJSON name}";
      runtime = c.runtime or [ ];
      scope = c.scope or { };
    in
    assert lib.assertMsg (lib.subtractLists allowed (builtins.attrNames c) == [ ])
      "nixsci validate: ${who} has unknown attributes ${builtins.toJSON (lib.subtractLists allowed (builtins.attrNames c))}";
    assert lib.assertMsg (builtins.isString name && builtins.match "[a-z0-9-]+" name != null)
      "nixsci validate: ${who}: name must match [a-z0-9-]+";
    assert lib.assertMsg (builtins.isString (c.property or null) && c.property != "")
      "nixsci validate: ${who}: property must be a non-empty string (the claim)";
    assert lib.assertMsg (builtins.isString (c.model or null) && c.model != "")
      "nixsci validate: ${who}: model must be a non-empty string (what the check is about)";
    assert lib.assertMsg (c ? subject) "nixsci validate: ${who}: subject is required (the code under check)";
    assert lib.assertMsg (c ? checker) "nixsci validate: ${who}: checker is required (the tool)";
    assert lib.assertMsg (builtins.isAttrs scope && scope != { } && builtins.all scalar (builtins.attrValues scope))
      "nixsci validate: ${who}: scope must be a non-empty attrset of numbers, strings, bools or lists";
    assert lib.assertMsg (builtins.isString (c.command or null) && c.command != "")
      "nixsci validate: ${who}: command is required (bash; it must write $out/outcome.json)";
    assert lib.assertMsg (builtins.isList runtime) "nixsci validate: ${who}: runtime must be a list of packages";
    let
      meta = pkgs.writeText "validate-${name}.json"
        (builtins.toJSON { inherit name; inherit (c) property model; inherit scope; });
      command = pkgs.writeText "validate-${name}-command.sh" c.command;
      verdict = pkgs.runCommand "verdict-${name}" { PYTHONDONTWRITEBYTECODE = "1"; } ''
        export PATH=${lib.makeBinPath ([ c.checker pkgs.coreutils ] ++ runtime)}
        export subject="${c.subject}"
        export checker="${c.checker}"
        mkdir -p $out
        ${pkgs.bash}/bin/bash -eu -o pipefail ${command}
        ${python}/bin/python -m nixsci.validate.judge --meta ${meta} --out "$out" \
          --subject "$subject" --checker "$checker"
      '';
      # The gate: fails unless the verdict holds. Its output is the verdict, so it is cached too.
      holds = pkgs.runCommand "holds-${name}" { PYTHONDONTWRITEBYTECODE = "1"; } ''
        ${python}/bin/python -m nixsci.validate.gate ${verdict}/verdict.json
        mkdir $out
        cp ${verdict}/verdict.json $out/verdict.json
      '';
    in
    { inherit name verdict holds; };

  suite = checks:
    let
      keyed = lib.mapAttrs
        (key: c: assert lib.assertMsg (c.name == key)
          "nixsci validate: suite key ${builtins.toJSON key} must equal its check's name ${builtins.toJSON c.name}"; c)
        checks;
    in
    {
      checks = lib.mapAttrs (_: c: c.holds) keyed;
      verdicts = lib.mapAttrs (_: c: c.verdict) keyed;
      # Depends on every verdict, not on the gates: the table builds when a check does not hold.
      table = pkgs.runCommand "validate-table" { PYTHONDONTWRITEBYTECODE = "1"; } ''
        mkdir $out
        ${python}/bin/python -m nixsci.validate.table $out \
          ${lib.concatMapStringsSep " " (c: "${c.verdict}/verdict.json") (lib.attrValues keyed)}
      '';
    };
in
{ inherit check suite; }
