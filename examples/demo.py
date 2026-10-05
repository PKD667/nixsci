"""A toy experiment: a loss curve per (epsilon, seed), recorded as typed rows."""

import random

import lab

rng = random.Random(lab.seed() or 0)
epsilon = lab.params().get("epsilon", 0.1)
for epoch in range(5):
    lab.record(
        "loss",
        {"epoch": epoch, "value": epsilon * 0.5**epoch + rng.random() * 0.01, "split": "train"},
    )
