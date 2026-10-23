"""Seeded generator of workspace trees."""
import random
from pathlib import Path

FLAKE = '''{
  inputs.nixsci.url = "path:NIXSCI";
  inputs.nixpkgs.follows = "nixsci/nixpkgs";
  outputs = inputs: inputs.nixsci.lib.workspace { inherit inputs; root = ./.; };
}
'''
NAMES = ["alpha", "beta", "gamma", "delta", "eps", "zeta"]
IGNORED = ["archive", "result-1", ".hidden", "node_modules"]


def gen(seed, root):
    rnd = random.Random(seed)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "flake.nix").write_text(FLAKE)
    projects = rnd.sample(NAMES, rnd.randint(1, 3))
    for p in projects:
        d = root / "proj" / p
        (d / "figures").mkdir(parents=True)
        (d / "figures" / "fig.tex").write_text("\\fbox{\\figwidth}\n")
        (d / "project.nix").write_text("{ }\n")
    templates = rnd.sample(NAMES, rnd.randint(0, 2))
    for t in templates:
        d = root / "tmpl" / t
        d.mkdir(parents=True)
        (d / "template.nix").write_text("{ }\n")
    docs = []
    for i in range(rnd.randint(1, 3)):
        group = rnd.choice(NAMES)
        rel = f"notes/{group}/doc{i}"
        d = root / rel
        (d / "tex").mkdir(parents=True)
        (d / "tex" / "main.tex").write_text("\\documentclass{article}\\begin{document}x\\end{document}\n")
        picks = rnd.sample(projects, rnd.randint(0, len(projects)))
        body = f'  projects = [ {" ".join(chr(34) + q + chr(34) for q in picks)} ];\n  src = "tex";\n'
        (d / "document.nix").write_text("{\n" + body + "}\n")
        docs.append((f"{group}-doc{i}", rel))
    for junk in IGNORED:
        j = root / junk / "junk"
        j.mkdir(parents=True)
        (j / "document.nix").write_text("{ this is not nix }\n")
    return docs
