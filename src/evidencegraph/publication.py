"""Publication uses immutable partitions and a single atomic manifest commit.

Unreferenced generations left by an interrupted process are harmless. Readers use
only the paths named by one manifest snapshot. No published partition is modified.
"""

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from evidencegraph.manifest import atomic_json, case_mutation_lock, read_manifest


@contextmanager
def case_publication_transaction(root: Path) -> Iterator[dict]:
    with case_mutation_lock(root):
        manifest = copy.deepcopy(read_manifest(root))
        yield manifest
        atomic_json(root / "manifest.json", manifest)
