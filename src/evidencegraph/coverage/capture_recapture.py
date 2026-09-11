"""Chapman estimate with a parametric hypergeometric percentile bootstrap.

Independence violations make the population non-identifiable: an apparent narrow
interval under the model is not a confidence bound on the real incident.
"""

import numpy as np

from evidencegraph.coverage.declared import validated_level
from evidencegraph.schema import CaptureRecapture

ASSUMPTIONS = (
    "Closed population",
    "Independent capture mechanisms",
    "Equal catchability within each witness",
    "Exact record linkage without false matches",
)


def chapman(
    n_a: int,
    n_b: int,
    m: int,
    *,
    seed: int = 0,
    n_boot: int = 2000,
    level: float = 0.95,
    witness_a: str = "A",
    witness_b: str = "B",
    unit_kind: str = "record",
    violations: tuple[str, ...] = (),
) -> CaptureRecapture:
    validated_level(level)
    if min(n_a, n_b, m) < 0 or m > min(n_a, n_b) or n_boot < 100:
        raise ValueError("invalid capture-recapture counts or bootstrap count")
    estimate = None
    bounds = None
    if m > 0:
        estimate = ((n_a + 1) * (n_b + 1) / (m + 1)) - 1
        population = max(n_a + n_b - m, round(estimate))
        overlap = np.random.default_rng(seed).hypergeometric(
            n_a, population - n_a, n_b, size=n_boot
        )
        draws = ((n_a + 1) * (n_b + 1) / (overlap + 1)) - 1
        interval = np.quantile(draws, [(1 - level) / 2, (1 + level) / 2])
        bounds = float(interval[0]), float(interval[1])
    else:
        violations += ("Zero overlap: no finite population estimate reported",)
    return CaptureRecapture(
        id=f"cr-{witness_a}-{witness_b}-{unit_kind}",
        witness_a=witness_a,
        witness_b=witness_b,
        unit_kind=unit_kind,
        n_a=n_a,
        n_b=n_b,
        m_both=m,
        n_hat_chapman=estimate,
        interval=bounds,
        level=level,
        assumptions=ASSUMPTIONS,
        assumption_violations=violations,
        interpretation=f"Non-identifiable under observed dependence; only observed union {n_a + n_b - m} is a lower bound. Model interval is not a population bound."
        if violations
        else "Conditional model estimate; parametric hypergeometric percentile bootstrap",
    )
