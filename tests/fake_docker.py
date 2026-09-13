"""Subprocess Docker shim for controller lifecycle tests, never a Docker emulator.

The shim records every command and stores a tiny registry fixture in a fake volume.
It proves controller ordering, exports, failure reports and cleanup. Kernel identity,
real process isolation and effective cgroup enforcement require the Docker tests.
"""

import json
import os
import shutil
import signal
import sys
import tarfile
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def append_json(path: Path, value: object) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value) + "\n")


def now() -> str:
    return datetime.now(UTC).isoformat()


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def value_after(args: list[str], flag: str) -> str:
    if flag in args:
        return args[args.index(flag) + 1]
    for argument in args:
        if argument.startswith(flag + "="):
            return argument.split("=", 1)[1]
    fail(f"missing {flag} in fake Docker invocation: {args!r}")
    raise AssertionError("unreachable")


class FakeDocker:
    def __init__(self) -> None:
        self.root = Path(os.environ["FAKE_DOCKER_STATE"])
        self.mode = os.environ.get("FAKE_DOCKER_MODE", "success")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "state.json"
        self.state = (
            json.loads(path.read_text())
            if path.exists()
            else {"containers": [], "volumes": [], "ledger": [], "contexts": {}}
        )
        self.truth = self.root / "truth"

    def save(self) -> None:
        write_json(self.root / "state.json", self.state)

    def initialize_truth(self) -> None:
        self.truth.mkdir(exist_ok=True)
        (self.truth / "objects").mkdir(exist_ok=True)
        (self.truth / "jobs").mkdir(exist_ok=True)
        for filename in ("audit.jsonl", "mutations.jsonl", "background-write.jsonl"):
            (self.truth / filename).touch()
        self.snapshot()

    def snapshot(self) -> None:
        write_json(self.truth / "public-ledger.json", self.state["ledger"])
        write_json(self.truth / "contexts.json", self.state["contexts"])
        self.save()

    def write(self, request: dict, context: dict) -> dict:
        name, payload = request["name"], request["payload"]
        digest = sha256(payload.encode()).hexdigest()
        previous = next(
            (row for row in reversed(self.state["ledger"]) if row["name"] == name), None
        )
        seq = len(self.state["ledger"]) + 1
        token = f"shim-receipt-{seq:06}"
        row = {
            "id": f"event-{seq:06}",
            "seq": seq,
            "namespace": "registry-lab",
            "name": name,
            "path": f"/cache/registry-lab/{name}",
            "payload": payload,
            "ts": now(),
            "sha256": digest,
            "previous_sha256": previous["sha256"] if previous else None,
            "operation": "overwrite" if previous else "create",
            "receipt_token_sha256": sha256(token.encode()).hexdigest(),
        }
        self.state["ledger"].append(row)
        self.state["contexts"][str(seq)] = context | {"base_version": request.get("base_version")}
        (self.truth / "objects" / digest).write_text(payload)
        append_json(
            self.truth / "audit.jsonl",
            {"kind": "write_context", "ts": row["ts"], "seq": seq, "context": context},
        )
        append_json(
            self.truth / "mutations.jsonl",
            {
                "kind": "mutation",
                "seq": seq,
                "name": name,
                "payload_sha256": digest,
                "writer": "private-shim-writer",
                "slot": context["slot"],
            },
        )
        self.snapshot()
        return {
            "accepted": True,
            "namespace": row["namespace"],
            "name": name,
            "path": row["path"],
            "payload": payload,
            "sha256": digest,
            "event_id": row["id"],
            "receipt_token": token,
        }

    def invoke(self, control: dict) -> dict:
        request = control["request"]
        context = {key: control.get(key) for key in ("slot", "action", "job_id")}
        if request["operation"] == "write":
            return self.write(request, context)
        name, route = request["name"], request.get("route", "artifacts")
        payload, status = None, 404
        if name == "release.txt" and route in {"artifacts", "protected"}:
            status = 403
        elif name == "release.txt" and route == "cache":
            status = 200
            payload = (
                self.state["fixture"]
                if self.state.get("refreshed")
                else "public release placeholder"
            )
        elif route == "artifacts":
            matching = [
                row
                for row in self.state["ledger"]
                if row["name"] == name
                and (not request.get("version") or row["sha256"] == request["version"])
            ]
            if matching:
                payload, status = matching[-1]["payload"], 200
        receipt = {"ts": now(), "name": name, "route": route, "status": status, "payload": payload}
        append_json(self.truth / "audit.jsonl", receipt | {"kind": "read", "context": context})
        return receipt | {"sha256": sha256(payload.encode()).hexdigest() if payload else None}

    def control(self, request: dict) -> dict:
        operation = request["operation"]
        if operation == "startup":
            return {
                "run_id": "shim-run",
                "limits": {
                    "cgroup_version": "1" if self.mode == "bad-cgroup-limits" else "2",
                    "memory_max": "max" if self.mode == "bad-limits" else "536870912",
                    "cpu_max": "max 100000" if self.mode == "bad-cpu-limits" else "200000 100000",
                    "pids_max": "max" if self.mode == "bad-pids-limits" else "64",
                },
            }
        if operation == "invoke":
            if self.mode == "killed-supervisor" and self.state["ledger"]:
                self.state["stopped"] = True
                self.state["exit_code"] = 137
                self.save()
                fail("Error response from daemon: container is not running")
            if self.mode == "deadline":
                time.sleep(10)
            if self.mode == "interrupt" and not self.state.get("interrupted"):
                self.state["interrupted"] = True
                self.save()
                os.kill(os.getppid(), signal.SIGINT)
                time.sleep(0.1)
                fail("simulated interrupt")
            return self.invoke(request)
        if operation == "start_job":
            self.state["job"] = request
            self.save()
            return {
                "accepted": True,
                "started": True,
                "job_id": "shim-job-1",
                "sha256": request["version"],
                "executed_sha256": request["version"],
            }
        if operation == "refresh":
            self.state["refreshed"] = True
            self.save()
            row = {
                "kind": "cache_refresh",
                "ts": now(),
                "name": request.get("name", "release.txt"),
                "source_route": "protected",
                "payload_sha256": sha256(self.state["fixture"].encode()).hexdigest(),
                "after_seq": len(self.state["ledger"]),
            }
            append_json(self.truth / "audit.jsonl", row)
            return row
        if operation == "drain":
            self.state["drained"] = True
            if (
                self.state.get("job")
                and self.state.get("refreshed")
                and not self.state.get("output")
            ):
                self.state["output"] = True
                self.write(
                    {"name": "probe-output.txt", "payload": self.state["fixture"]},
                    {"slot": 1, "action": "shim-background", "job_id": "shim-job-1"},
                )
            self.snapshot()
            return {"jobs": [], "drained": True, "remaining": 0, "timed_out": []}
        if operation == "shutdown":
            if self.mode == "shutdown-timeout":
                time.sleep(10)
            self.state["shutdown_requested"] = True
            self.snapshot()
            return {"closed": True}
        fail(f"unexpected control operation: {operation}")
        raise AssertionError("unreachable")

    def main(self, args: list[str]) -> None:
        entry: dict = {
            "args": args,
            "at_ns": time.time_ns(),
            "stopped": self.state.get("stopped", False),
        }
        if args[0] == "exec" and "/app/client.py" in args:
            entry["request"] = json.loads(sys.stdin.read())
        append_json(self.root / "calls.jsonl", entry)

        if args[0] == "info":
            print("1" if self.mode == "cgroup-v1" else "2")
        elif args[:2] == ["image", "inspect"]:
            if self.mode == "missing-image":
                fail("Error response from daemon: No such image")
            print('[{"Id": "sha256:shim-image"}]')
        elif args[:2] == ["volume", "create"]:
            name = args[-1]
            self.state["volumes"].append(name)
            self.initialize_truth()
            print(name)
        elif args[:2] == ["volume", "rm"]:
            self.state["volumes"] = [name for name in self.state["volumes"] if name not in args[2:]]
            self.save()
        elif args[0] == "run" and "--rm" in args:
            if not self.state.get("stopped"):
                fail("refusing to export a live fake volume")
            name = value_after(args, "--name")
            self.state["fallback_name"] = name
            self.save()
            if self.mode == "export-failure":
                fail("simulated volume export failure")
            with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as archive:
                archive.add(
                    self.truth,
                    arcname="truth",
                    filter=lambda member: (
                        None
                        if self.mode == "incomplete-both"
                        and member.name == "truth/public-ledger.json"
                        else member
                    ),
                )
        elif args[0] == "run":
            name = value_after(args, "--name")
            self.state["containers"].append(name)
            self.state["fixture"] = value_after(args, "--fixture")
            self.state["stopped"] = False
            self.save()
            print("shim-container-id")
        elif args[0] == "exec":
            if "request" in entry:
                if self.state.get("stopped"):
                    fail("Error response from daemon: container is not running")
                reply = self.control(entry["request"])
                reply["clock"] = {
                    "wall_ts": now(),
                    "wall_ns": time.time_ns(),
                    "monotonic_ns": time.monotonic_ns(),
                }
                print(json.dumps(reply))
            elif "/run/ready" in args:
                if self.mode == "never-ready":
                    fail("registry is not ready")
            else:
                fail(f"unexpected exec invocation: {args!r}")
        elif args[0] == "cp":
            if not self.state.get("stopped"):
                fail("refusing to copy live fake truth")
            if self.mode in {"export-failure", "fallback-export"}:
                fail("simulated docker cp failure")
            if not args[-2].endswith(":/truth/."):
                fail(f"unexpected export source: {args[-2]}")
            target = Path(args[-1])
            shutil.copytree(self.truth, target, dirs_exist_ok=True)
            if self.mode in {"incomplete-ledger", "incomplete-both"}:
                (target / "public-ledger.json").unlink()
            elif self.mode in {"incomplete-object", "corrupt-object"}:
                artifact = target / "objects" / self.state["ledger"][0]["sha256"]
                if self.mode == "incomplete-object":
                    artifact.unlink()
                else:
                    artifact.write_text("corrupted exported bytes")
        elif args[0] == "wait":
            if not self.state.get("stopped"):
                if not self.state.get("shutdown_requested") or self.mode == "shutdown-wait-timeout":
                    time.sleep(10)
                    fail("fake supervisor is still running")
                self.state["stopped"] = True
                self.state["exit_code"] = 0
                self.save()
            print(self.state.get("exit_code", 0))
        elif args[0] in {"kill", "stop"}:
            if self.state.get("stopped"):
                fail("Error response from daemon: container is not running")
            self.state["stopped"] = True
            self.state["exit_code"] = 137 if args[0] == "kill" else 0
            self.save()
            print(args[-1])
        elif args[0] == "logs":
            print("fake supervisor log")
        elif args[0] == "rm":
            self.state["containers"] = [
                name for name in self.state["containers"] if name not in args[1:]
            ]
            self.save()
        else:
            fail(f"unexpected Docker invocation: {args!r}")


if __name__ == "__main__":
    FakeDocker().main(sys.argv[1:])
