# /// script
# requires-python = ">=3.13"
# dependencies = ["matplotlib==3.10.8"]
# ///
"""Render the paired results as standalone publication figures."""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def plot(results: Path, out: Path) -> None:
    data = json.loads(results.read_text())
    if not data["passed"]:
        raise ValueError("do not publish a passing figure from failed checks")
    rows = {r["scenario"]: r for r in data["rows"] if r["seed"] == data["seeds"][0]}
    relay = rows["full_receipt_relay"]
    control = relay["controls"]["implicit_token_exclusivity"]
    entries = [("Relay / previous rule", control["correct"], control["confident_errors"])]
    for label, key in (
        ("Relay / current default", "full_receipt_relay"),
        ("Relay / false exclusivity", "false_exclusivity"),
        ("Clean / explicit exclusivity", "clean"),
        ("Clean / unknown exclusivity", "shared_tokens_no_attack"),
    ):
        p = rows[key]["score"]["produced"]
        entries.append((label, p["correct"], p["confident_errors"]))
    plt.rcParams.update(
        {
            "font.size": 11,
            "svg.fonttype": "none",
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    fig, ax = plt.subplots(figsize=(9, 3.6), layout="constrained")
    colors = ("#287d83", "#b84032", "#dce1e8")
    for i, (_label, correct, wrong) in enumerate(entries):
        left = 0
        for j, count in enumerate((correct, wrong, 12 - correct - wrong)):
            ax.barh(i, count, left=left, height=0.62, color=colors[j])
            if count:
                ax.text(
                    left + count / 2,
                    i,
                    str(count),
                    ha="center",
                    va="center",
                    color="white" if j < 2 else "#26334d",
                    fontweight="bold",
                )
            left += count
    ax.set_yticks(range(len(entries)), [e[0] for e in entries])
    ax.invert_yaxis()
    ax.set_xlim(0, 12)
    ax.set_xticks(range(0, 13, 2))
    ax.set_xlabel("Registry records (same 12 mutations within each paired comparison)")
    ax.spines[["left", "bottom"]].set_visible(False)
    ax.tick_params(length=0)
    ax.legend(
        [plt.Rectangle((0, 0), 1, 1, color=c) for c in colors],
        ["Correct supported", "Wrong supported", "Unattributed"],
        loc="lower left",
        bbox_to_anchor=(0, 1.02),
        ncol=3,
        frameon=False,
    )
    out.mkdir(parents=True, exist_ok=True)
    for extension in ("png", "svg", "pdf"):
        fig.savefig(out / f"receipt-relay.{extension}", dpi=220, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    plot(args.results, args.out)
