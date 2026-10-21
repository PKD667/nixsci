# stats

`nixsci.stats` judges a claim on measured samples at a requested confidence level. It returns
`supported`, `contradicted`, or `inconclusive`. A reproduction supports the same claim when both
the original and the new measurements support it. This is not a test that two datasets are equal.

```python
from nixsci import stats

b = stats.mean_bounds([1.4, 1.5, 1.6], level=0.99)
print(stats.above(b, 1.0))
```

| Quantity | Method | Assumption |
|---|---|---|
| Mean | Student t | Independent normal samples; approximation for other distributions |
| Difference of means | Welch t | Independent samples; approximate coverage |
| Median | Order statistics | Independent observations of a continuous distribution |
| Proportion | Wilson score | Independent Bernoulli trials; approximate coverage |
| Difference of proportions | Newcombe score | Two independent Bernoulli samples; approximate coverage |
| Exact value | Every raw value equals the expected value | No statistical model |

`level` is the requested two-sided interval coverage. The whole interval must lie inside a
claim's region to support it, outside to contradict it; an overlapping interval is inconclusive.
These are method-specific intervals, not distribution-free guarantees. Confidence applies to each claim separately; there is
no multiple-claim correction. Hardware is recorded, but does not gate comparisons.

## Claims in lab

An analysis can declare claims beside its pipelines:

```nix
claims.speedup = {
  kind = "above";
  of = { use = "bench"; dataset = "timing"; column = "speedup"; stat = "mean"; };
  than = 1.5;
};
confidence = 0.99;
```

The analysis writes `claims.json` (`{ "format": "2.0", "claims": { ... } }`) and
`claims-report.json`. Claim names are their IDs within the analysis. Additional fields on a claim,
such as a source label or formal statement, travel with the report but do not constitute a proof.
Reports explicitly mark formal verification as not performed. Unsupported and inconclusive claims
remain visible in the report; they do not erase the PDF or stop an analysis build.

```
nixsci lab judge claims.json --view ORIGINAL --reproduction NEW --level 0.99 --json report.json
```

Exit codes: 0 for supported/reproduced, 1 for a contradiction or an unsupported original claim,
3 for inconclusive results. Invalid declarations or data fail with an error.

A selection names `use`, `dataset`, `column`, optional `where` equalities, and `stat` (`mean`,
`median`, or `proportion`). By default it averages matching rows within each run, then treats runs
as independent samples. A proportion becomes one proportion per run, judged with a t interval.
`unit = "row"` requires `independent = true`; only then do bool rows use Wilson/Newcombe intervals.
Exact claims always check raw rows, without aggregation.

Claim kinds: `above`/`below` a `than` threshold; `between` `lo` and `hi`; `near` a `value` with
an absolute `margin` or `{ relative = r; }`; `greater`/`less` than another selection, optionally
by a difference `by`; `exact`ly a `value`. Mean and proportion support two-selection comparisons;
median does not. Pairing, repeated-measures models, and joint confidence are not implemented.

## Checks

On MIST, from the repository's pinned environment:

```
nix develop --command pytest -q stats/tests
```

Tests compare t quantiles with R 4.6.1, exercise simulated coverage with fixed seeds, check
confidence monotonicity with Hypothesis, and judge Parquet views. They check an implementation
under its stated model; they do not establish that an experiment satisfies that model.
