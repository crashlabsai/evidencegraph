"""Portable BagIt directories with complete inventories and semantic verification.

Inventory/path rules are adapted from Crossledger export.bundle. The bundle is a
directory; callers can archive it using their own transport after verification.
"""

import json
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from evidencegraph.manifest import case_mutation_lock, read_manifest
from evidencegraph.provenance import sha256_file, verify_file_identity
from evidencegraph.schema import Relation
from evidencegraph.store import Store, safe_path, validate_graph

MAX_BUNDLE_FILES = 100_000
MAX_BUNDLE_BYTES = 8 * 1024 * 1024 * 1024


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or any(p in {"..", "."} for p in value.split("/"))
    ):
        raise ValueError(f"unsafe bundle path: {value}")
    return path


def _inventory(root: Path) -> list[Path]:
    paths = []
    size = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("bundle contains a symbolic link")
        if path.is_dir():
            continue
        if not path.is_file() or path.stat().st_nlink > 1:
            raise ValueError("bundle contains a non-regular or hard-linked file")
        _safe_relative_path(path.relative_to(root).as_posix())
        paths.append(path)
        size += path.stat().st_size
        if len(paths) > MAX_BUNDLE_FILES or size > MAX_BUNDLE_BYTES:
            raise ValueError("bundle exceeds inventory limits")
    return paths


def _manifest_entries(path: Path) -> dict[str, str]:
    entries = {}
    for line in path.read_text().splitlines():
        if len(line) < 67 or line[64:66] != "  " or not re.fullmatch(r"[0-9a-f]{64}", line[:64]):
            raise ValueError("invalid checksum manifest entry")
        name = line[66:]
        _safe_relative_path(name)
        if name in entries:
            raise ValueError("duplicate checksum entry")
        entries[name] = line[:64]
    return entries


def _write_payload_manifest(root: Path) -> None:
    entries = [
        f"{sha256_file(path)}  {path.relative_to(root).as_posix()}\n"
        for path in _inventory(root / "data")
    ]
    # Paths returned above are absolute when root is absolute; relative_to(root) remains portable.
    (root / "manifest-sha256.txt").write_text("".join(entries))


def case_files(manifest: dict) -> set[str]:
    files = {"case.json", "manifest.json"}
    files.update(w["snapshot_path"] for w in manifest["witnesses"].values())
    for partition in manifest["partitions"].values():
        files.update(entry["path"] for entry in partition.values())
    files.update(entry["path"] for entry in manifest.get("reports", {}).values())
    if manifest.get("validation"):
        files.add(manifest["validation"]["path"])
    files.update(manifest.get("scout", {}).get("files", {}))
    return files


def export_bundle(case: Path, dest: Path) -> dict:
    case = case.absolute()
    dest = dest.absolute()
    if dest.exists():
        raise ValueError("bundle destination already exists")
    if dest.is_relative_to(case):
        raise ValueError("export destination must be outside the case")
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".eg-bundle-", dir=dest.parent))
    try:
        with case_mutation_lock(case):
            manifest = read_manifest(case)
            if not manifest.get("reports"):
                raise ValueError("render a current docket before export")
            for name in sorted(case_files(manifest)):
                source = safe_path(case, name)
                target = temporary / "data" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            (temporary / "bagit.txt").write_text(
                "BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n"
            )
            (temporary / "bag-info.txt").write_text(
                "Bag-Software-Agent: evidencegraph 0.1\nExternal-Description: Evidence graph and docket; private truth excluded\n"
            )
            _write_payload_manifest(temporary)
            tags = ("bagit.txt", "bag-info.txt", "manifest-sha256.txt")
            (temporary / "tagmanifest-sha256.txt").write_text(
                "".join(f"{sha256_file(temporary / name)}  {name}\n" for name in tags)
            )
            result = verify_bundle(temporary, recompute=True)
        temporary.rename(dest)
        return {"bundle": str(dest), **result}
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def verify_bundle(dest: Path, *, recompute: bool = False) -> dict:
    inventory = {p.relative_to(dest).as_posix(): p for p in _inventory(dest)}
    required = {"bagit.txt", "bag-info.txt", "manifest-sha256.txt", "tagmanifest-sha256.txt"}
    if not required.issubset(inventory):
        raise ValueError("incomplete BagIt tags")
    if (
        dest / "bagit.txt"
    ).read_text() != "BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n":
        raise ValueError("unsupported BagIt declaration")
    tags = _manifest_entries(dest / "tagmanifest-sha256.txt")
    if set(tags) != required - {"tagmanifest-sha256.txt"}:
        raise ValueError("incomplete tag inventory")
    payload = _manifest_entries(dest / "manifest-sha256.txt")
    if any(not name.startswith("data/") for name in payload):
        raise ValueError("payload outside data directory")
    if set(inventory) != set(payload) | required:
        raise ValueError("bundle inventory differs from checksum manifests")
    for name, digest in (tags | payload).items():
        if sha256_file(inventory[name]) != digest:
            raise ValueError(f"bundle hash mismatch: {name}")
    root = dest / "data"
    manifest = read_manifest(root)
    if set(payload) != {"data/" + name for name in case_files(manifest)}:
        raise ValueError("case manifest does not account for every payload file")
    for witness in manifest["witnesses"].values():
        verify_file_identity(
            safe_path(root, witness["snapshot_path"]),
            expected_size=witness["size_bytes"],
            expected_sha256=witness["sha256"],
            subject=witness["witness_id"],
        )
    for partition in manifest["partitions"].values():
        for entry in partition.values():
            verify_file_identity(
                safe_path(root, entry["path"]),
                expected_size=None,
                expected_sha256=entry["sha256"],
                subject="graph partition",
            )
    for entry in [
        *manifest.get("reports", {}).values(),
        *([manifest["validation"]] if manifest.get("validation") else []),
    ]:
        verify_file_identity(
            safe_path(root, entry["path"]),
            expected_size=None,
            expected_sha256=entry["sha256"],
            subject="report",
        )
    for path, digest in manifest.get("scout", {}).get("files", {}).items():
        verify_file_identity(
            safe_path(root, path),
            expected_size=None,
            expected_sha256=digest,
            subject="Scout database",
        )
    with Store(root, manifest) as store:
        validate_graph(store)
        for row in store.query("SELECT * FROM relations"):
            Relation.model_validate(row)
    if recompute:
        from evidencegraph.docket.render import compute_docket

        report = manifest.get("reports", {}).get("docket.json")
        if not report:
            raise ValueError("no docket available for recomputation")
        expected = json.loads(safe_path(root, report["path"]).read_text())
        actual = compute_docket(root, manifest)
        if actual != expected:
            raise ValueError("docket recomputation differs from bundled result")
    return {
        "verified": True,
        "recomputed": recompute,
        "payload_files": len(payload),
        "payload_manifest_sha256": sha256_file(dest / "manifest-sha256.txt"),
    }
