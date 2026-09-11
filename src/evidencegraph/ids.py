"""Content-scoped stable identifiers; paths and claimed handles are never actor ids."""

from evidencegraph.provenance import sha256_text


def witness_id(sha256: str) -> str:
    return f"w-{sha256[:16]}"


def entity_id(kind: str, witness: str, locator: str) -> str:
    return "e-" + sha256_text(f"{kind}\0{witness}\0{locator}")[:24]


def relation_id(kind: str, subject: str, obj: str, method: str) -> str:
    return "r-" + sha256_text(f"{kind}\0{subject}\0{obj}\0{method}")[:32]


def fragment_reference_id(witness: str, transcript: str, kind: str, locator: str) -> str:
    # Exact locator identity rule from Crossledger scout_import.
    return "ref-" + sha256_text(f"{witness}\0{transcript}\0{kind}\0{locator}")[:32]
