"""Checker for tinysort.sort. For each n in 0..6, every permutation of 0..n-1 must sort to 0..n-1.

Reads the subject from $subject and writes $out/outcome.json. A raise inside sort is a failure
too. One counterexample per length (the first failing permutation) goes to $out/counterexamples/.
"""

import importlib.util
import itertools
import json
import os
from pathlib import Path

out = Path(os.environ["out"])
spec = importlib.util.spec_from_file_location("subject", os.environ["subject"])
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)

counterexamples = []
for n in range(7):
    for index, xs in enumerate(itertools.permutations(range(n))):
        xs = list(xs)
        try:
            got = subject.sort(list(xs))
        except Exception as error:
            got = f"raised {error!r}"
        if got != sorted(xs):
            cid = f"n{n}-{index}"
            (out / "counterexamples").mkdir(exist_ok=True)
            (out / "counterexamples" / f"{cid}.txt").write_text(f"input    {xs}\nexpected {sorted(xs)}\ngot      {got!r}\n")
            counterexamples.append({"id": cid, "artifact": f"counterexamples/{cid}.txt"})
            break

(out / "outcome.json").write_text(json.dumps({
    "holds": not counterexamples,
    "gaps": [{"id": "lengths", "why": "lengths above 6 not explored"}],
    "counterexamples": counterexamples,
}))
