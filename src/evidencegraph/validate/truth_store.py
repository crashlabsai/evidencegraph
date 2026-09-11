"""Private truth boundary. Imported only by validation and validation-key generation."""

import json
from pathlib import Path

import duckdb


def host_key(truth: Path) -> dict:
    host = truth / "host" if (truth / "host").is_dir() else truth
    contexts = json.loads((host / "contexts.json").read_text())
    binding_path = truth / "bindings.jsonl"
    bindings = [json.loads(line) for line in binding_path.read_text().splitlines()]
    actions = {b["tag"]: b for b in bindings if b.get("event_uuid")}
    if len(actions) != sum(bool(b.get("event_uuid")) for b in bindings):
        raise ValueError("duplicate host dispatch tags")
    produced = {}
    for seq, context in contexts.items():
        if context["action"] not in actions:
            raise ValueError("host dispatch has no native tool binding")
        binding = actions[context["action"]]
        produced[f"event-{int(seq):06}"] = {
            "event_uuid": binding["event_uuid"] if context.get("job_id") is None else None,
            "launch_uuid": binding["event_uuid"] if context.get("job_id") else None,
            "job_id": context.get("job_id"),
            "label": binding.get("label"),
            "slot": context["slot"],
        }
    spoofed = (
        json.loads((truth / "spoofed.json").read_text())
        if (truth / "spoofed.json").exists()
        else []
    )
    audit = [json.loads(line) for line in (host / "audit.jsonl").read_text().splitlines()]
    mutations = [json.loads(line) for line in (host / "mutations.jsonl").read_text().splitlines()]
    # An ephemeral, separate database demonstrates the storage boundary: never attach it to Store.
    with duckdb.connect(":memory:") as private:
        private.execute("CREATE TABLE host_bindings (tag VARCHAR PRIMARY KEY, event_uuid VARCHAR)")
        private.executemany(
            "INSERT INTO host_bindings VALUES (?,?)",
            [(tag, b["event_uuid"]) for tag, b in actions.items()],
        )
    return {"produced": produced, "spoofed": spoofed, "audit": audit, "mutations": mutations}
