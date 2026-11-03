# A document's `meta` checked against the slots of its template: a missing required slot, or a slot the
# template does not have, is an evaluation error naming the document and the slot. `meta = null` in a
# template accepts anything.
{ lib }:
{ name, template, meta }:
let
  t = template;
  required = lib.attrNames (lib.filterAttrs (_: v: v == "required") t.meta);
  metaErrors =
    map (k: "${k} is required") (lib.filter (k: !(meta ? ${k})) required)
    ++ map (k: "${k} is not a slot of ${t.name} (slots: ${lib.concatStringsSep ", " (lib.attrNames t.meta)})")
      (lib.filter (k: !(t.meta ? ${k})) (lib.attrNames meta));
in
if t.meta == null || metaErrors == [ ] then meta else throw "doc: ${name}: meta: ${lib.concatStringsSep "; " metaErrors}"
