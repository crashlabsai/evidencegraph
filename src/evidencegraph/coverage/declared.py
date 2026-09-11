"""Crossledger coverage semantics, using vectorized equivalent Bernoulli bootstraps."""

import math
from collections.abc import Callable, Mapping, Sequence

import numpy as np

from evidencegraph.schema import CoverageEstimate, CoverageKind, Outcome, Population


def validated_level(level: float) -> float:
    if not math.isfinite(level) or not 0 < level < 1:
        raise ValueError("interval level must be finite and between zero and one")
    return level


def bootstrap_interval(
    units: Sequence[int], statistic: Callable, *, seed: int, n_boot: int = 1000, level: float = 0.95
) -> tuple[float, float] | None:
    validated_level(level)
    if n_boot < 100:
        raise ValueError("at least 100 bootstrap replicates are required")
    if not units:
        return None
    rng = np.random.default_rng(seed)
    values = [
        statistic(rng.choice(units, len(units), replace=True).tolist()) for _ in range(n_boot)
    ]
    bounds = np.quantile(values, [(1 - level) / 2, (1 + level) / 2])
    return float(bounds[0]), float(bounds[1])


def proportion_interval(
    n: int, k: int, *, seed: int, n_boot: int = 2000, level: float = 0.95
) -> tuple[float, float] | None:
    validated_level(level)
    if not 0 <= k <= n or n_boot < 100:
        raise ValueError("invalid bootstrap counts")
    if n == 0:
        return None
    # Resampling an empirical binary population has exactly this distribution.
    draws = np.random.default_rng(seed).binomial(n, k / n, size=n_boot) / n
    bounds = np.quantile(draws, [(1 - level) / 2, (1 + level) / 2])
    return float(bounds[0]), float(bounds[1])


def message_weighted(
    rows: Sequence[dict],
    population: Population,
    *,
    reference_source: str,
    seed: int,
    level: float = 0.95,
    **kwargs,
) -> CoverageEstimate:
    assessable = [r for r in rows if r["outcome"] != Outcome.NOT_ASSESSABLE]
    n = len(assessable)
    k = sum(r["outcome"] == Outcome.SUPPORTED for r in assessable)
    return CoverageEstimate(
        id=f"cov-records-{population.id}",
        kind=CoverageKind.MESSAGE_WEIGHTED,
        reference_source=reference_source,
        population=population,
        sampling_unit="record",
        design="Full enumeration; percentile bootstrap over exchangeable observed records",
        estimate=k / n if n else None,
        interval=proportion_interval(n, k, seed=seed, level=level),
        interval_level=level,
        n_population=len(rows),
        n_sampled=n,
        n_supported=k,
        exclusions=(f"{len(rows) - n} not assessable records",),
        assumptions=(
            "Bootstrap describes resampling variability, not unobserved evidence or attribution error",
            "Records are treated as exchangeable; dropped sessions can cause clustered missingness",
        ),
        **kwargs,
    )


def unique_author(
    rows: Sequence[dict],
    population: Population,
    author_by_entry: Mapping[str, str | None],
    *,
    reference_source: str,
    seed: int,
    level: float = 0.95,
    covered_units: set[str] | None = None,
) -> CoverageEstimate:
    units = {v for v in author_by_entry.values() if v}
    present = (
        covered_units
        if covered_units is not None
        else {
            author_by_entry.get(r["subject_id"]) for r in rows if r["outcome"] == Outcome.SUPPORTED
        }
    )
    n, k = len(units), len(units & present)
    return CoverageEstimate(
        id=f"cov-authors-{population.id}",
        kind=CoverageKind.UNIQUE_AUTHOR,
        reference_source=reference_source,
        population=population,
        sampling_unit="claimed label",
        design="Full enumeration; percentile bootstrap over label units",
        estimate=k / n if n else None,
        interval=proportion_interval(n, k, seed=seed, level=level),
        interval_level=level,
        n_population=len(author_by_entry),
        n_sampled=n,
        n_supported=k,
        assumptions=(
            "Labels are not actors; blank labels excluded",
            "Coverage of a label means at least one record is present",
        ),
    )
