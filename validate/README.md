# validate

Verdicts for checks whose subject is deterministic: a compile, a bounded model check, a seeded
fuzzer, a program on a deterministic scheduler. Such a check gives the same verdict every time it
runs, so Nix's cache is the truth: the build is the check, and the store holds its verdict.

`nixsci.lab` is for the other kind. It records measurements. A run is a sample, and Nix cannot
cache a sample, because a cached sample is not a new sample. Some checks are not samples either.

**Dividing line: a check goes to validate when its subject is deterministic. It goes to lab when
it touches anything that is not (real threads, a network, real hardware), even if its output is
pass/fail.** A pass on real hardware bounds a failure rate; that is a sample. Determinism is itself
checkable: `nix build --rebuild` rebuilds a verdict and compares the bytes.

## What a verdict says

A verdict is worthless without its scope, as a timing is without its machine. Every verdict states:

- **property**: what is claimed;
- **model**: what the check is about. This is not the thing itself;
- **subject**: the code under check (a store path);
- **checker**: the tool (a store path);
- **scope**: what was explored;
- **outcome**: `holds`, the known `gaps` of the checker, and the `counterexamples` (id and artifact).

Gaps are listed, never hidden. They do not change `holds`.

No R, no statistics, no lock file, no executor: a verdict is a derivation of the current code, so it
cannot be stale.

## The Nix API

```nix
v = nixsci.lib.validate { inherit pkgs; };
c = v.check {
  name = "rejections";                 # [a-z0-9-]+
  property = "...";                    # the claim
  model = "...";                       # what the check is about
  subject = ./code.py;                 # exported to the command as $subject
  checker = pkgs.python3;              # on PATH, and exported as $checker
  scope = { cases = 93; seeds = [ 1 2 3 ]; };
  command = ''...'';                   # bash; must write $out/outcome.json
  runtime = [ ];                       # optional extra packages on PATH
};
# c.verdict  builds verdict.json; the command must succeed.
# c.holds    fails unless the verdict holds; this is what goes in checks.<system>.
s = v.suite { rejections = c; };     # { checks; verdicts; table; }
```

The command writes `$out/outcome.json` with exactly three keys:

```json
{ "holds": true,
  "gaps": [ { "id": "lengths", "why": "lengths above 6 not explored" } ],
  "counterexamples": [ { "id": "n2-0", "artifact": "counterexamples/n2-0.txt" } ] }
```

The build (`judge.py`) refuses, with a message, when: a key is missing or extra; `holds` is not a
bool; `holds` is true with counterexamples; `holds` is false without one; an artifact is absent or
lies outside `$out`. A command that exits non-zero gives no verdict. Evaluation refuses a bad name,
an empty property, model or scope, and a missing command.

`verdict.json` has sorted keys and no timestamp, so a rebuild is byte-identical.

`suite.table` builds `verdicts.json` and `verdicts.md` from every verdict. It depends on the
verdicts, not on the gates, so it builds when a check does not hold.

## The CLI

```sh
nixsci validate [--flake REF] [NAME...]      # build, print the table, exit 1 unless all hold
nixsci validate --rebuild NAME...            # build, then rebuild and compare the bytes
```

The table has the columns of `verdicts.md`. `--rebuild` builds first, because Nix refuses to check
an output it has never built. A difference names the check and says it belongs in lab; exit 1.
Errors from nix are printed as nix printed them.

## Examples

`examples/` holds a sort function checked on every permutation of length 0 to 6. `sorted` holds.
`broken` rotates its output, fails, and keeps its counterexamples. `checks.<system>.validate-sorted`
is the example that `nix flake check` gates; `broken` is in `validate.<system>.verdicts` so the
failing path is visible, and it is not in `checks`.

## What validate does not do

- No statistics. A verdict says that a claim holds over a scope, not how often it fails.
- No impure checks. Real threads, networks and hardware go to lab.
- No proof that the checker is right. A verdict is as good as its checker, which is why the checker
  is a pinned derivation, and why its scope and gaps are printed next to the result.
- The model is not the thing. A check of `tinysort.sort` on lengths 0 to 6 says nothing about
  lengths above 6; the gap says so.

Tests: `nix develop --command pytest -q validate/tests` (the judge rules run without nix; the rest
call nix and skip when it is absent).
