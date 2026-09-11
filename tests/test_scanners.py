import json

import pytest
from test_lab_stage import load_stage

from evidencegraph.lab.stage import construct_stage
from evidencegraph.manifest import read_manifest
from evidencegraph.scanners.validation import run_scan, scout_database, validation_csv
from evidencegraph.store import Store


def test_scout_control_repeats_spoofs_and_is_scored_as_wrong(tmp_path):
    stage = tmp_path / "stage"
    case = tmp_path / "case"
    construct_stage(stage, seed=12, spoof="B:2", drop=["A"])
    load_stage(case, stage)
    keys = stage / "private" / "validation.csv"
    validation_csv(case, stage / "private", keys)
    result = run_scan(case, scanner="claimed-writes", validation=keys)
    assert result["annotations"] == 3
    with Store(case, read_manifest(case)) as store:
        metrics = [
            json.loads(row["validation"])
            for row in store.query("SELECT validation FROM annotations")
        ]
        assert sum(m["false_positive"] for m in metrics) == 2
        assert sum(m["true_positive"] for m in metrics) == 9
    assert scout_database(case)["transcripts"] == 3


def test_paid_scanner_cannot_accidentally_run(tmp_path):
    with pytest.raises(ValueError, match="Paid execution"):
        run_scan(
            tmp_path,
            scanner="claimed-writes",
            validation=tmp_path / "none",
            model="live/provider",
            max_usd=20,
        )
