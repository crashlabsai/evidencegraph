import shutil

import pytest

from evidencegraph.docket.render import render
from evidencegraph.export.bagit import export_bundle, verify_bundle


def test_portable_bundle_recomputes_and_detects_tamper(wiki_case, tmp_path):
    render(wiki_case)
    bundle = tmp_path / "bundle"
    result = export_bundle(wiki_case, bundle)
    assert result["verified"] and result["recomputed"]
    portable = tmp_path / "moved"
    shutil.move(bundle, portable)
    shutil.rmtree(wiki_case)
    assert verify_bundle(portable, recompute=True)["verified"]
    next((portable / "data" / "evidence").rglob("revisions.jsonl")).write_text("changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_bundle(portable)


def test_unlisted_files_and_symlinks_fail(wiki_case, tmp_path):
    render(wiki_case)
    bundle = tmp_path / "bundle"
    export_bundle(wiki_case, bundle)
    (bundle / "extra.txt").write_text("unlisted")
    with pytest.raises(ValueError, match="inventory"):
        verify_bundle(bundle)
    (bundle / "extra.txt").unlink()
    (bundle / "extra.txt").symlink_to("/etc/hosts")
    with pytest.raises(ValueError, match="symbolic"):
        verify_bundle(bundle)
