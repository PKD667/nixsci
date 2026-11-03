# One \nixsci@defval line per scalar of a project's values file (tree.nix runs this over each one).
#
#   jq -r --arg p "<project>/<key>" -f values.jq values.json
#
# A string is escaped for TeX, so a `%`, `_` or `&` in an analysis's output cannot end the line, close a
# brace or break typesetting. A number or a boolean prints as JSON prints it. A name that cannot be a
# TeX macro name (`%`, `#`, `\`, `{` or `}` in a key) is an error, at build time, naming it.

def tex: [ explode[] | [.] | implode
  | if . == "\\" then "\\textbackslash{}"
    elif . == "^" then "\\textasciicircum{}"
    elif . == "~" then "\\textasciitilde{}"
    elif . == "$" then "\\char36{}"   # \$ is TS1: a font a minimal TeX Live has only as bitmaps
    elif test("[{}%#&_]") then "\\" + .
    else . end ] | join("");

def val: if type == "string" then tex else tostring end;

def checked: if test("[%#\\\\{}]") then error("value key \(.) has a character TeX cannot take in a name") else . end;

def line($id; $v): "\\nixsci@defval{\($id | checked)}{\($v | val)}";

if type == "object" or type == "array"
then paths(scalars) as $k | line("\($p).\($k | map(tostring) | join("."))"; getpath($k))
else line($p; .)
end
