import pytest

from evidencegraph.coverage.capture_recapture import chapman
from evidencegraph.coverage.declared import proportion_interval


def test_known_population_and_seed():
    estimate = chapman(50, 40, 20, seed=9)
    assert estimate.n_hat_chapman is not None
    assert estimate.interval is not None
    assert abs(estimate.n_hat_chapman - 100) < 5
    assert estimate.interval[0] <= 100 <= estimate.interval[1]
    assert estimate == chapman(50, 40, 20, seed=9)


def test_dependent_capture_not_mislabelled_as_population_bound():
    estimate = chapman(50, 50, 50, violations=("same source",))
    assert estimate.assumption_violations
    assert "not a population bound" in estimate.interpretation


def test_empty_and_invalid_counts():
    assert chapman(10, 20, 0).n_hat_chapman is None
    assert proportion_interval(0, 0, seed=0) is None
    with pytest.raises(ValueError):
        chapman(1, 2, 3)
    with pytest.raises(ValueError):
        proportion_interval(5, 6, seed=0)
    with pytest.raises(ValueError):
        chapman(5, 5, 2, level=float("nan"))
