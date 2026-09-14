import json

import pytest

from evidencegraph.manifest import read_manifest
from evidencegraph.validate.stress import SCENARIOS, run_stress


def test_paired_stress_pack_reproduces_counterexample_and_abstention_cost(tmp_path):
    out = tmp_path / "stress"
    result = run_stress(out, seeds=(73,), bundles=False)
    assert result["passed"] and not result["failures"]
    rows = {r["scenario"]: r for r in result["rows"]}
    assert set(rows) == {s.id for s in SCENARIOS}
    relay, false = rows["full_receipt_relay"], rows["false_exclusivity"]
    assert relay["public_inventory_sha256"] == false["public_inventory_sha256"]
    assert relay["case_config_sha256"] != false["case_config_sha256"]
    assert relay["methods"] == {"receipt_possession_only": 10, "key_and_version": 2}
    assert relay["score"]["produced"]["confident_errors"] == 0
    assert relay["controls"]["implicit_token_exclusivity"]["confident_errors"] == 1
    assert false["score"]["produced"]["confident_errors"] == 1
    clean, shared = rows["clean"], rows["shared_tokens_no_attack"]
    assert clean["public_inventory_sha256"] == shared["public_inventory_sha256"]
    assert clean["score"]["produced"]["correct"] == 12
    assert shared["score"]["produced"]["correct"] == 0
    for row in rows.values():
        witnesses = read_manifest(out / row["case"])["witnesses"]
        assert all("/private/" not in w["origin"] for w in witnesses.values())
        assert row["score"]["spoofed"]["false_positives"] == 0
    # The capture-edge fixture is an internally possible collection window.
    public = out / "views" / "73" / "window-edge" / "public"
    population = json.loads((public / "population.json").read_text())
    records = [json.loads(line) for line in (public / "ledger.jsonl").read_text().splitlines()]
    assert all(population["started_at"] <= r["ts"] <= population["stopped_at"] for r in records)
    assert json.loads((out / "results.json").read_text())["passed"]
    with pytest.raises(ValueError, match="new output directory"):
        run_stress(out, seeds=(73,))


@pytest.mark.parametrize("seeds", [(), (0, 0)])
def test_invalid_seed_sets_do_not_create_output(tmp_path, seeds):
    out = tmp_path / "invalid"
    with pytest.raises(ValueError, match="distinct seed"):
        run_stress(out, seeds=seeds)
    assert not out.exists()
