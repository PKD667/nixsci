import json
import math
import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nixsci import stats
from nixsci.stats import claims


# Student t quantiles, from R 4.6.1: print(qt(p, df), digits = 10). df = 2.5 is a Welch-style
# fractional value.
@pytest.mark.parametrize("p,df,expected", [
    (0.975, 1, 12.7062047), (0.975, 10, 2.2281389), (0.95, 5, 2.0150484),
    (0.995, 30, 2.7499957), (0.975, 2.5, 3.574654842),
])
def test_t_quantile_matches_r(p, df, expected):
    assert stats.t_quantile(p, df) == pytest.approx(expected, abs=1e-6)


def test_t_quantile_infinite_df_is_normal():
    assert stats.t_quantile(0.975, math.inf) == pytest.approx(1.959964, abs=1e-6)


@given(st.floats(0.51, 0.999), st.floats(0.5, 200))
def test_t_cdf_inverts_quantile(p, df):
    assert stats.t_cdf(stats.t_quantile(p, df), df) == pytest.approx(p, abs=1e-8)


def _coverage(draw, bound, trials=4000, seed=1):
    rng = random.Random(seed)
    return sum(bound(draw(rng)) for _ in range(trials)) / trials


def test_mean_lower_bound_coverage():
    # two-sided at 0.95: the lower bound exceeds the true mean in about 2.5 % of samples
    miss = _coverage(lambda r: [r.gauss(10, 3) for _ in range(5)],
                     lambda xs: stats.mean_bounds(xs, 0.95).lo > 10)
    assert 0.025 - 0.012 < miss < 0.025 + 0.012


def test_wilson_coverage():
    miss = _coverage(lambda r: [r.random() < 0.3 for _ in range(40)],
                     lambda xs: stats.bounds(xs, "proportion", 0.95).hi < 0.3)
    assert miss < 0.025 + 0.015


def test_welch_coverage_unequal_variances():
    miss = _coverage(lambda r: ([r.gauss(5, 1) for _ in range(4)], [r.gauss(5, 4) for _ in range(9)]),
                     lambda ab: stats.welch_bounds(ab[0], ab[1], 0.95).lo > 0)
    assert 0.025 - 0.012 < miss < 0.025 + 0.012


def test_median_bounds_small_n():
    # n = 6: each tail at the extremes is 1/64 < 0.025, so their coverage exceeds 0.95.
    b = stats.median_bounds([3, 1, 2, 5, 4, 6], 0.95)
    assert (b.estimate, b.lo, b.hi) == (3.5, 1, 6)
    # n = 5: P(at least one tail missed) = 2/32 > 0.05 -> unbounded.
    b = stats.median_bounds([1, 2, 3, 4, 5], 0.95)
    assert math.isinf(b.lo) and math.isinf(b.hi)
    assert stats.above(b, 0) == "inconclusive"


def test_one_sample_cannot_support():
    b = stats.mean_bounds([7.0], 0.95)
    assert stats.above(b, 0) == "inconclusive"


def test_identical_replicates_are_a_point():
    b = stats.mean_bounds([2.0, 2.0, 2.0], 0.99)
    assert (b.lo, b.hi) == (2.0, 2.0)
    assert stats.between(b, 2.0, 2.0) == "supported"


samples = st.lists(st.floats(-1e3, 1e3, allow_nan=False), min_size=2, max_size=30)


@settings(max_examples=200)
@given(samples, st.floats(-1e3, 1e3))
def test_verdicts_exclusive_and_monotone_in_level(xs, t):
    strict = stats.above(stats.mean_bounds(xs, 0.99), t)
    loose = stats.above(stats.mean_bounds(xs, 0.95), t)
    # a verdict reached at 0.99 is reached at 0.95: raising the level only adds inconclusives
    if strict != "inconclusive":
        assert loose == strict


@given(samples, st.floats(-1e3, 1e3), st.floats(0, 1e3))
def test_between_requires_interval_inside(xs, centre, width):
    b = stats.mean_bounds(xs, 0.95)
    lo, hi = centre - width, centre + width
    both = stats.above(b, lo) == "supported" or b.lo >= lo
    both = both and (stats.below(b, hi) == "supported" or b.hi <= hi)
    assert (stats.between(b, lo, hi) == "supported") == both


def test_reproduced_table():
    assert stats.reproduced("supported", "supported") == "yes"
    assert stats.reproduced("supported", "contradicted") == "no"
    assert stats.reproduced("supported", "inconclusive") == "inconclusive"
    assert stats.reproduced("inconclusive", "supported") == "unsupported"


def test_level_outside_range():
    with pytest.raises(ValueError):
        stats.mean_bounds([1.0, 2.0], 0.4)


# ---- claims over a view ----------------------------------------------------------------------

def _view(tmp_path, name, rows_per_run):
    import pyarrow as pa
    import pyarrow.parquet as pq

    d = tmp_path / name / "demo" / "timing"
    d.mkdir(parents=True)
    for run, rows in rows_per_run.items():
        table = {"run": [run] * len(rows), "n": [r[0] for r in rows],
                 "impl": [r[1] for r in rows], "seconds": [r[2] for r in rows],
                 "ok": [r[3] for r in rows]}
        pq.write_table(pa.table(table), d / f"{run}.parquet")
    return tmp_path / name


def _runs(rng, fast, slow, k=6):
    return {f"r{i}": [(1, "new", rng.gauss(fast, 0.05), True), (1, "old", rng.gauss(slow, 0.05), True)]
            for i in range(k)}


CLAIMS = {
    "faster": {"kind": "less",
               "of": {"use": "demo", "dataset": "timing", "column": "seconds", "where": {"impl": "new"}},
               "than": {"use": "demo", "dataset": "timing", "column": "seconds", "where": {"impl": "old"}}},
    "about-one-second": {"kind": "near", "value": 1.0, "margin": {"relative": 0.1},
                         "of": {"use": "demo", "dataset": "timing", "column": "seconds", "where": {"impl": "new"}}},
    "always-ok": {"kind": "exact", "value": True,
                  "of": {"use": "demo", "dataset": "timing", "column": "ok"}},
}


def test_judge_reproduced(tmp_path):
    rng = random.Random(3)
    a = _view(tmp_path, "a", _runs(rng, 1.0, 1.5))
    b = _view(tmp_path, "b", _runs(rng, 1.01, 1.5))
    report = claims.judge(CLAIMS, a, b, 0.99)
    assert {n: e["reproduced"] for n, e in report["claims"].items()} == {
        "about-one-second": "yes", "always-ok": "yes", "faster": "yes"}
    assert claims.status(report) == 0


def test_judge_not_reproduced(tmp_path):
    rng = random.Random(4)
    a = _view(tmp_path, "a", _runs(rng, 1.0, 1.5))
    b = _view(tmp_path, "b", _runs(rng, 1.8, 1.5))  # the new code got slower than the old
    report = claims.judge(CLAIMS, a, b, 0.95)
    assert report["claims"]["faster"]["reproduced"] == "no"
    assert report["claims"]["about-one-second"]["reproduced"] == "no"
    assert claims.status(report) == 1


def test_judge_inconclusive_with_two_replicates(tmp_path):
    rng = random.Random(5)
    a = _view(tmp_path, "a", _runs(rng, 1.0, 1.5))
    b = _view(tmp_path, "b", _runs(rng, 1.0, 1.5, k=1))
    report = claims.judge(CLAIMS, a, b, 0.95)
    assert report["claims"]["about-one-second"]["reproduced"] == "inconclusive"
    assert claims.status(report) == 3


def test_cli_writes_report(tmp_path, capsys):
    rng = random.Random(6)
    a = _view(tmp_path, "a", _runs(rng, 1.0, 1.5))
    (tmp_path / "claims.json").write_text(json.dumps(CLAIMS))
    code = claims.main([str(tmp_path / "claims.json"), "--view", str(a), "--level", "0.99",
                        "--json", str(tmp_path / "r.json")])
    assert code == 3  # six replicates cannot resolve the near claim at 99 %
    assert json.loads((tmp_path / "r.json").read_text())["level"] == 0.99
    assert "faster" in capsys.readouterr().out


def test_exact_checks_raw_rows_not_the_run_average(tmp_path):
    v = _view(tmp_path, "exact", {"r1": [(1, "new", 1.0, True), (1, "new", 3.0, False)]})
    claim = {"kind": "exact", "value": 2.0,
             "of": {"use": "demo", "dataset": "timing", "column": "seconds"}}
    assert claims.evaluate(claim, v, 0.95)["verdict"] == "contradicted"


def test_proportion_defaults_to_runs_not_correlated_rows(tmp_path):
    v = _view(tmp_path, "prop", {"r1": [(1, "new", 1.0, True)] * 10000})
    claim = {"kind": "above", "than": 0.9,
             "of": {"use": "demo", "dataset": "timing", "column": "ok", "stat": "proportion"}}
    r = claims.evaluate(claim, v, 0.95)
    assert r["n"] == 1 and r["verdict"] == "inconclusive"
    claim["of"]["unit"] = "row"
    with pytest.raises(ValueError, match="independent"):
        claims.evaluate(claim, v, 0.95)
    claim["of"]["independent"] = True
    r = claims.evaluate(claim, v, 0.95)
    assert r["n"] == 10000 and r["method"] == "wilson"


@pytest.mark.parametrize("x", [math.nan, math.inf, -math.inf])
def test_nonfinite_samples_rejected(x):
    with pytest.raises(ValueError, match="finite"):
        stats.mean_bounds([x, 1.0], 0.95)


def test_bool_proportion_rejects_continuous_samples():
    with pytest.raises(ValueError, match="bool"):
        stats.bounds([0.25, 0.75], "proportion", 0.95)


def test_claim_format_compatibility(tmp_path):
    a = _view(tmp_path, 'format', _runs(random.Random(7), 1.0, 1.5))
    legacy = claims.judge(CLAIMS, a, None, 0.95)
    current = claims.judge({'format': '2.3', 'claims': CLAIMS}, a, None, 0.95)
    assert current == legacy
    assert current['formalVerification'] == 'not-performed'
    with pytest.raises(ValueError, match='unsupported claims format'):
        claims.judge({'format': '3.0', 'claims': CLAIMS}, a, None, 0.95)
