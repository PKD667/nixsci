"""Does a set of measurements support a claim, at a given confidence?

A claim is a statement about a quantity measured by an experiment: "the mean speedup is above
1.5", "A is faster than B", "the loss is within 0.01 of 0.25". `judge` answers it with one of three
verdicts:

    supported      the data support the claim at confidence `level`
    contradicted   the data support the claim's negation at confidence `level`
    inconclusive   neither: too few samples, or the quantity sits at the claim's boundary

`level` (0.95, 0.99, ...) is the confidence of the verdict. Each bound below is one-sided at
`level`, so a two-sided claim (`between`, `near`) is the two one-sided tests of equivalence
testing (TOST) and its printed interval has two-sided coverage 2 * level - 1. Confidence is method-specific, not a universal error bound. Student t is exact for independent
normal samples; Welch, Wilson and Newcombe give approximate confidence. The conditions are:

    mean        Student t over samples (independent, roughly normal or n large)
    proportion  Wilson score (independent Bernoulli trials)
    median      order statistics (independent, continuous); may be unbounded for small n
    difference of means        Welch, Welch-Satterthwaite degrees of freedom
    difference of proportions  Newcombe's hybrid score (from two Wilson intervals)

The same function judges an original experiment and its reproduction: a reproduction supports the
same claim when both verdicts are `supported`. Standard library only.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from statistics import NormalDist
from typing import Sequence

VERDICTS = ("supported", "contradicted", "inconclusive")
STATS = ("mean", "median", "proportion")


# ---- distributions --------------------------------------------------------------------------

def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction of the incomplete beta function (modified Lentz)."""
    tiny, eps = 1e-300, 1e-15
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 10_000):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            return h
    raise ArithmeticError("incomplete beta did not converge")


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    """P(T <= t) for Student's t with `df` (> 0, may be fractional) degrees of freedom."""
    if math.isinf(df):
        return NormalDist().cdf(t)
    tail = 0.5 * betainc(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - tail if t > 0 else tail


def t_quantile(p: float, df: float) -> float:
    """The t with P(T <= t) = p, by bisection to 1e-12."""
    if df <= 0 or math.isnan(df):
        raise ValueError("degrees of freedom must be positive")
    if not 0.0 < p < 1.0:
        raise ValueError(f"quantile of probability {p}")
    if math.isinf(df):
        return NormalDist().inv_cdf(p)
    if p < 0.5:
        return -t_quantile(1.0 - p, df)
    lo, hi = 0.0, 1.0
    while t_cdf(hi, df) < p:
        hi *= 2.0
    while hi - lo > 1e-12 * max(1.0, hi):
        mid = (lo + hi) / 2.0
        lo, hi = (mid, hi) if t_cdf(mid, df) < p else (lo, mid)
    return (lo + hi) / 2.0


def z_quantile(p: float) -> float:
    return NormalDist().inv_cdf(p)


def binom_sf(k: int, n: int, p: float = 0.5) -> float:
    """P(X >= k) for X ~ Binomial(n, p)."""
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(max(k, 0), n + 1))


# ---- one quantity: estimate and one-sided bounds --------------------------------------------

@dataclass(frozen=True)
class Bounds:
    """An estimate with a lower and an upper bound, each one-sided at `level`.

    A bound is +-inf when the data cannot bound the quantity at that level (n too small).
    """
    estimate: float
    lo: float
    hi: float
    level: float
    n: int
    method: str

    def as_dict(self) -> dict:
        return {k: (None if isinstance(v, float) and math.isinf(v) else v) for k, v in asdict(self).items()}


def _finite(xs: Sequence[float]) -> list[float]:
    values = [float(x) for x in xs]
    if not all(math.isfinite(x) for x in values):
        raise ValueError("samples must be finite (no NaN or infinity)")
    return values


def _check_level(level: float) -> None:
    if not 0.5 < level < 1.0:
        raise ValueError(f"level {level} must be between 0.5 and 1 (e.g. 0.95, 0.99)")


def mean_bounds(xs: Sequence[float], level: float) -> Bounds:
    _check_level(level)
    xs = _finite(xs)
    n = len(xs)
    if n == 0:
        raise ValueError("no samples")
    m = math.fsum(xs) / n
    if n == 1:
        return Bounds(m, -math.inf, math.inf, level, n, "t")
    s = math.sqrt(math.fsum((x - m) ** 2 for x in xs) / (n - 1))
    half = t_quantile(level, n - 1) * s / math.sqrt(n)
    return Bounds(m, m - half, m + half, level, n, "t")


def wilson_bounds(successes: int, n: int, level: float) -> Bounds:
    _check_level(level)
    if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
        raise ValueError("n must be a positive integer")
    if isinstance(successes, bool) or not isinstance(successes, int) or not 0 <= successes <= n:
        raise ValueError("successes must be an integer between 0 and n")
    p, z = successes / n, z_quantile(level)
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return Bounds(p, max(0.0, centre - half), min(1.0, centre + half), level, n, "wilson")


def median_bounds(xs: Sequence[float], level: float) -> Bounds:
    """Distribution-free bounds from order statistics: x_(k) <= median with probability
    P(Binomial(n, 1/2) >= k); the lower bound is the largest such x_(k) with that probability
    at least `level` (and symmetrically for the upper bound)."""
    _check_level(level)
    ys = sorted(_finite(xs))
    n = len(ys)
    if n == 0:
        raise ValueError("no samples")
    med = ys[n // 2] if n % 2 else (ys[n // 2 - 1] + ys[n // 2]) / 2
    k = max((k for k in range(1, n + 1) if binom_sf(k, n) >= level), default=None)
    lo = ys[k - 1] if k is not None else -math.inf
    hi = ys[n - k] if k is not None else math.inf
    return Bounds(med, lo, hi, level, n, "order statistics")


def bounds(xs: Sequence[float], stat: str, level: float) -> Bounds:
    if stat == "mean":
        return mean_bounds([float(x) for x in xs], level)
    if stat == "median":
        return median_bounds([float(x) for x in xs], level)
    if stat == "proportion":
        if any(type(x) is not bool for x in xs):
            raise ValueError("proportion requires bool samples")
        return wilson_bounds(sum(1 for x in xs if x), len(xs), level)
    raise ValueError(f"statistic {stat!r}; one of {STATS}")


# ---- the difference of two quantities (a - b) -----------------------------------------------

def welch_bounds(a: Sequence[float], b: Sequence[float], level: float) -> Bounds:
    _check_level(level)
    a, b = _finite(a), _finite(b)
    na, nb = len(a), len(b)
    if na == 0 or nb == 0:
        raise ValueError("no samples")
    ma, mb = math.fsum(a) / na, math.fsum(b) / nb
    d = ma - mb
    if na < 2 or nb < 2:
        return Bounds(d, -math.inf, math.inf, level, na + nb, "welch")
    va = math.fsum((x - ma) ** 2 for x in a) / (na - 1) / na
    vb = math.fsum((x - mb) ** 2 for x in b) / (nb - 1) / nb
    se = math.sqrt(va + vb)
    if se == 0.0:
        return Bounds(d, d, d, level, na + nb, "welch")
    df = (va + vb) ** 2 / ((va * va / (na - 1) if va else 0.0) + (vb * vb / (nb - 1) if vb else 0.0))
    half = t_quantile(level, df) * se
    return Bounds(d, d - half, d + half, level, na + nb, "welch")


def newcombe_bounds(a: Sequence[bool], b: Sequence[bool], level: float) -> Bounds:
    wa = wilson_bounds(sum(1 for x in a if x), len(a), level)
    wb = wilson_bounds(sum(1 for x in b if x), len(b), level)
    d = wa.estimate - wb.estimate
    lo = d - math.sqrt((wa.estimate - wa.lo) ** 2 + (wb.hi - wb.estimate) ** 2)
    hi = d + math.sqrt((wa.hi - wa.estimate) ** 2 + (wb.estimate - wb.lo) ** 2)
    return Bounds(d, lo, hi, level, len(a) + len(b), "newcombe")


def difference_bounds(a: Sequence[float], b: Sequence[float], stat: str, level: float) -> Bounds:
    if stat == "mean":
        return welch_bounds([float(x) for x in a], [float(x) for x in b], level)
    if stat == "proportion":
        if any(type(x) is not bool for x in [*a, *b]):
            raise ValueError("proportion requires bool samples")
        return newcombe_bounds(a, b, level)
    raise ValueError(f"a difference of {stat!r} is not supported; use mean or proportion")


# ---- verdicts --------------------------------------------------------------------------------

def above(b: Bounds, threshold: float) -> str:
    """The quantity is greater than `threshold`."""
    if not math.isfinite(threshold):
        raise ValueError("claim threshold must be finite")
    if b.lo > threshold:
        return "supported"
    if b.hi <= threshold:
        return "contradicted"
    return "inconclusive"


def below(b: Bounds, threshold: float) -> str:
    """The quantity is less than `threshold`."""
    if not math.isfinite(threshold):
        raise ValueError("claim threshold must be finite")
    if b.hi < threshold:
        return "supported"
    if b.lo >= threshold:
        return "contradicted"
    return "inconclusive"


def between(b: Bounds, lo: float, hi: float) -> str:
    """The quantity lies in [lo, hi] (equivalence: both one-sided tests must pass)."""
    if not math.isfinite(lo) or not math.isfinite(hi):
        raise ValueError("claim limits must be finite")
    if lo > hi:
        raise ValueError(f"between: lo {lo} > hi {hi}")
    if b.lo >= lo and b.hi <= hi:
        return "supported"
    if b.hi < lo or b.lo > hi:
        return "contradicted"
    return "inconclusive"


def exact(xs: Sequence, value) -> str:
    """Every sample equals `value`: no statistics, one differing sample contradicts."""
    if not xs:
        raise ValueError("no samples")
    return "supported" if all(x == value for x in xs) else "contradicted"


def reproduced(original: str, reproduction: str) -> str:
    """Whether a reproduction supports the claim its original supported.

    yes            both supported
    no             the reproduction contradicts the claim
    inconclusive   anything else: re-measure with more replicates
    unsupported    the original never supported its own claim; nothing to reproduce
    """
    if original != "supported":
        return "unsupported"
    if reproduction == "supported":
        return "yes"
    if reproduction == "contradicted":
        return "no"
    return "inconclusive"
