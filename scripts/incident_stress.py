"""Build the offline incident evidence stress pack, without models or credentials."""

import argparse
from pathlib import Path

from evidencegraph.validate.stress import run_stress

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument("--no-bundles", action="store_true")
    args = parser.parse_args()
    result = run_stress(args.out, seeds=tuple(args.seeds), bundles=not args.no_bundles)
    print(args.out / "RESULTS.md")
    raise SystemExit(0 if result["passed"] else 1)
