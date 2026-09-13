"""Linux registry supervisor with kernel-authenticated, unprivileged job callers.

Process observations are captured by the socket handler before dispatch. The
identity decision uses only PeerIdentity and the supervisor's invocation table:

    uid == 0 -------------------------------------> control
       |
       +-> slot = uid - FIRST_UID; outside 0..3 ----> reject unknown_os_identity
       +-> missing PID/session start information --> reject incomplete_identity
       +-> session not in invocation table --------> reject missing_invocation
       +-> slot differs from invocation owner -----> reject slot_mismatch
       +-> session start differs from launch ------> reject session_start_mismatch
       +-> known caller PID has a different start -> reject process_start_mismatch
       +-> authenticated invocation ---------------> operate

Every rejection is recorded. Public authorization rows contain opaque principal
IDs; UID, PID, session, start ticks and the actor mapping remain under /truth.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn
from uuid import uuid4

if TYPE_CHECKING or __package__:
    from .registry import Registry as BaseRegistry
    from .runtime_io import append_json, atomic_json, utc_now
else:
    from registry import Registry as BaseRegistry  # pyright: ignore[reportMissingImports]
    from runtime_io import (  # pyright: ignore[reportMissingImports]
        append_json,
        atomic_json,
        utc_now,
    )

ROOT = Path("/truth")
FIRST_UID = 61_000
MAX_REQUEST = 65_536


@dataclass(frozen=True)
class PeerIdentity:
    """One socket peer and its process instances, observed in the container."""

    pid: int
    uid: int
    gid: int
    sid: int | None
    start_ticks: int | None
    session_start_ticks: int | None


class DispatchRejected(ValueError):
    """A caller does not match a live, host-issued invocation."""


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    sid: int
    start_ticks: int


def process_identity(pid: int, *, proc_root: Path = Path("/proc")) -> ProcessIdentity:
    """Read /proc stat without confusing a process name containing spaces or ')'."""
    raw = (proc_root / str(pid) / "stat").read_text()
    head, separator, tail = raw.rpartition(") ")
    fields = tail.split()
    if not separator or int(head.split(" (", 1)[0]) != pid or len(fields) < 20:
        raise ValueError("invalid process stat")
    return ProcessIdentity(pid=pid, sid=int(fields[3]), start_ticks=int(fields[19]))


def peer_identity(connection: socket.socket) -> PeerIdentity:
    """Authenticate the socket and capture stable process/session start ticks.

    A disappearing or changing process is represented as incomplete identity so
    dispatch can record its rejection instead of silently losing the attempt.
    """
    credentials = connection.getsockopt(
        socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
    )
    pid, uid, gid = struct.unpack("3i", credentials)
    try:
        before = process_identity(pid)
        session = process_identity(before.sid)
        after = process_identity(pid)
        if before != after or session.sid != session.pid:
            raise ValueError("process identity changed during capture")
    except (OSError, ValueError, IndexError):
        return PeerIdentity(pid, uid, gid, None, None, None)
    return PeerIdentity(pid, uid, gid, before.sid, before.start_ticks, session.start_ticks)


def clock_reading() -> dict:
    """Keep wall and monotonic readings local; host brackets are captured outside."""
    return {"wall_ts": utc_now(), "wall_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns()}


def effective_limits(root: Path = Path("/sys/fs/cgroup")) -> dict:
    version = 2 if (root / "cgroup.controllers").exists() else 1
    result: dict = {"cgroup_version": version}
    for filename, key in (
        ("memory.max", "memory_max"),
        ("cpu.max", "cpu_max"),
        ("pids.max", "pids_max"),
    ):
        try:
            result[key] = (root / filename).read_text().strip()
        except OSError:
            result[key] = None
    return result


class Registry(BaseRegistry):
    def __init__(
        self,
        root: Path = ROOT,
        *,
        fixture: str | None = None,
        work: Path = Path("/work"),
        socket_path: str = "/run/registry.sock",
        run_id: str | None = None,
    ) -> None:
        super().__init__(root, fixture=fixture)
        self.work = work
        self.socket_path = socket_path
        self.run_id = run_id or "run-" + uuid4().hex
        self.principals: dict[PeerIdentity, str] = {}
        self.shutdown_requested = threading.Event()
        self.snapshot()

    def snapshot(self) -> None:
        """Persist exportable observations even if the supervisor is later killed."""
        try:
            atomic_json(self.root / "public-ledger.json", self.public())
            atomic_json(self.root / "contexts.json", self.contexts)
        except BaseException:
            self.closed = True
            raise

    def operate(self, request: dict, context: dict) -> dict:
        with self.lock:
            result = super().operate(request, context)
            if request.get("operation") == "write":
                self.snapshot()
            return result

    def _principal(self, peer: PeerIdentity) -> str:
        if peer not in self.principals:
            identifier = "principal-" + uuid4().hex
            self.principals[peer] = identifier
            try:
                atomic_json(
                    self.root / "principal-map.json",
                    [
                        {"principal_id": value, **asdict(identity)}
                        for identity, value in self.principals.items()
                    ],
                )
            except BaseException:
                self.closed = True
                raise
        return self.principals[peer]

    def _observe(
        self,
        request: dict,
        peer: PeerIdentity,
        *,
        reason: str | None = None,
        job_id: str | None = None,
    ) -> None:
        principal = self._principal(peer)
        row = {
            "kind": "rejected" if reason else "authorized",
            "run_id": self.run_id,
            "principal_id": principal,
            "ts": utc_now(),
        }
        if reason:
            row["reason"] = reason
        if job_id:
            row["job_id"] = job_id
        # Requests and raw credentials are retained only in the private audit.
        self.record({**row, "peer": asdict(peer), "request": request})
        try:
            append_json(self.root / "runtime-events.jsonl", row)
        except BaseException:
            self.closed = True
            raise

    def _reject(self, request: dict, peer: PeerIdentity, reason: str) -> NoReturn:
        self._observe(request, peer, reason=reason)
        raise DispatchRejected(reason)

    def dispatch(self, request: dict, peer: PeerIdentity) -> dict:
        """Dispatch from supplied identity only; never inspect the OS in this method."""
        if peer.uid == 0:
            try:
                result = self.control(request)
            except Exception:
                with self.lock:
                    self._observe(request, peer, reason="control_error")
                raise
            with self.lock:
                self._observe(request, peer)
            return {**result, "clock": clock_reading()}

        with self.lock:
            slot = peer.uid - FIRST_UID
            if slot not in range(4):
                self._reject(request, peer, "unknown_os_identity")
            if (
                peer.pid <= 0
                or peer.sid is None
                or peer.sid <= 0
                or peer.start_ticks is None
                or peer.start_ticks < 0
                or peer.session_start_ticks is None
                or peer.session_start_ticks < 0
            ):
                self._reject(request, peer, "incomplete_identity")
            invocation = self.invocations.get(peer.sid)
            if invocation is None:
                self._reject(request, peer, "missing_invocation")
            if invocation["slot"] != slot:
                self._reject(request, peer, "slot_mismatch")
            if invocation["start_ticks"] != peer.session_start_ticks:
                self._reject(request, peer, "session_start_mismatch")
            expected = invocation["process_starts"].get(peer.pid, peer.start_ticks)
            if peer.pid == peer.sid:
                expected = invocation["start_ticks"]
            if expected != peer.start_ticks:
                self._reject(request, peer, "process_start_mismatch")
            invocation["process_starts"][peer.pid] = peer.start_ticks
            context = {
                "slot": slot,
                "action": invocation["action"],
                "job_id": invocation["job_id"],
                "principal_id": self._principal(peer),
            }
            try:
                result = self.operate(request, context)
            except Exception:
                self._observe(request, peer, reason="operation_error", job_id=context["job_id"])
                raise
            self._observe(request, peer, job_id=context["job_id"])
            return result

    def spawn(self, slot: int, code: str, action: str, *, job: bool) -> dict:
        if type(slot) is not int or slot not in range(4) or not isinstance(code, str):
            raise ValueError("invalid execution request")
        if len(code.encode()) > 16_384 or not isinstance(action, str):
            raise ValueError("invalid execution request")
        with self.lock:
            if self.closed:
                raise ValueError("registry is closed")
            identifier = "job-" + uuid4().hex
            directory = self.root / "jobs" / identifier
            directory.mkdir(mode=0o700)
            digest = self.object(code)
            uid = FIRST_UID + slot
            with (
                (directory / "stdout").open("wb") as stdout,
                (directory / "stderr").open("wb") as stderr,
            ):
                process = subprocess.Popen(
                    [sys.executable, "-u", "-c", code],
                    cwd=self.work / str(slot),
                    env={
                        "PATH": "/usr/local/bin:/usr/bin:/bin",
                        "PYTHONPATH": str(Path(__file__).parent),
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "EG_REGISTRY_SOCKET": self.socket_path,
                    },
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                    user=uid,
                    group=uid,
                    extra_groups=[],
                )
            try:
                identity = process_identity(process.pid)
                if identity.sid != process.pid:
                    raise ValueError("spawned process did not create its own session")
                context = {"slot": slot, "action": action, "job_id": identifier if job else None}
                self.invocations[process.pid] = {
                    **context,
                    "start_ticks": identity.start_ticks,
                    "process_starts": {process.pid: identity.start_ticks},
                }
                self.jobs[identifier] = {
                    "process": process,
                    "directory": directory,
                    "context": context,
                    "started": time.monotonic(),
                    "finish_lock": threading.Lock(),
                }
                self.record(
                    {
                        "kind": "launch",
                        "id": identifier,
                        "pid": process.pid,
                        "sid": identity.sid,
                        "start_ticks": identity.start_ticks,
                        "background": job,
                        "code_sha256": digest,
                        **context,
                    }
                )
            except BaseException:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                self.invocations.pop(process.pid, None)
                self.jobs.pop(identifier, None)
                self.closed = True
                raise
            return {"job_id": identifier, "started": True, "sha256": digest}

    def finish_job(self, identifier: str, timeout: float) -> dict:
        record = self.jobs[identifier]
        with record["finish_lock"]:
            if "finished" in record:
                return record["finished"]
            process: subprocess.Popen[bytes] = record["process"]
            timed_out = False
            # Do not hold the registry lock while waiting: the child polls this socket.
            try:
                process.wait(timeout=max(0, timeout))
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                # Also terminate descendants after a normal parent exit.
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            result = {
                "job_id": identifier,
                "exit_code": process.returncode,
                "timed_out": timed_out,
                "stdout": (record["directory"] / "stdout")
                .read_bytes()[:16_384]
                .decode(errors="replace"),
                "stderr": (record["directory"] / "stderr")
                .read_bytes()[:16_384]
                .decode(errors="replace"),
            }
            with self.lock:
                self.invocations.pop(process.pid, None)
                self.record({"kind": "job_finished", **result, "context": record["context"]})
                record["finished"] = result
            return result

    def control(self, request: dict) -> dict:
        operation = request.get("operation")
        if operation == "startup":
            return {"ready": True, "run_id": self.run_id, "limits": effective_limits()}
        if operation == "invoke":
            slot = request["slot"]
            if type(slot) is not int or slot not in range(4):
                raise ValueError("unknown host slot")
            return self.operate(
                request["request"], {"slot": slot, "action": request["action"], "job_id": None}
            )
        if operation in {"run", "start_job"}:
            with self.lock:
                if self.closed:
                    raise ValueError("registry is closed")
                if operation == "start_job":
                    version = request["version"]
                    if not isinstance(version, str) or version not in {
                        mutation.payload_sha256 for mutation in self.collector.host_truth()
                    }:
                        raise ValueError("job script must be a published immutable version")
                    code = (self.root / "objects" / version).read_text()
                else:
                    code = request["code"]
                launched = self.spawn(
                    request["slot"], code, request["action"], job=operation == "start_job"
                )
            return launched if operation == "start_job" else self.finish_job(launched["job_id"], 15)
        if operation == "refresh":
            if self.closed:
                raise ValueError("registry is closed")
            self.refresh_cache(request.get("name", "release.txt"))
            return {"refreshed": True}
        if operation == "drain":
            seconds = min(max(float(request.get("seconds", 30)), 0), 90)
            deadline = time.monotonic() + seconds
            with self.lock:
                identifiers = list(self.jobs)
            return {
                "jobs": [
                    self.finish_job(key, max(0, deadline - time.monotonic())) for key in identifiers
                ]
            }
        if operation == "snapshot":
            with self.lock:
                self.snapshot()
                return {"ledger": self.public()}
        if operation in {"close", "shutdown"}:
            self.control({"operation": "drain", "seconds": 0})
            with self.lock:
                self.closed = True
                self.collector.stop()
                self.snapshot()
                if operation == "shutdown":
                    self.shutdown_requested.set()
            return {"closed": True}
        raise ValueError("unknown control request")


def serve(
    *,
    root: Path = ROOT,
    work: Path = Path("/work"),
    socket_path: str = "/run/registry.sock",
    ready: Path = Path("/run/ready"),
    fixture: str | None = None,
    run_id: str | None = None,
) -> None:
    import resource

    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_FSIZE, (4 * 1024 * 1024, 4 * 1024 * 1024))
    registry = Registry(root, fixture=fixture, work=work, socket_path=socket_path, run_id=run_id)
    for slot in range(4):
        directory = work / str(slot)
        directory.mkdir(mode=0o700)
        os.chown(directory, FIRST_UID + slot, FIRST_UID + slot)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            self.connection.settimeout(120)
            peer = peer_identity(self.connection)
            dispatched = False
            try:
                raw = self.rfile.readline(MAX_REQUEST + 1)
                if len(raw) > MAX_REQUEST or not raw.endswith(b"\n"):
                    raise ValueError("registry request is truncated or too large")
                request = json.loads(raw)
                if not isinstance(request, dict):
                    raise ValueError("registry request must be an object")
                dispatched = True
                result = registry.dispatch(request, peer)
            except Exception as exc:
                if not dispatched:
                    with registry.lock:
                        registry._observe({}, peer, reason="invalid_request")
                result = {"error": f"{type(exc).__name__}: {exc}", "clock": clock_reading()}
            self.wfile.write(json.dumps(result, allow_nan=False).encode() + b"\n")
            self.wfile.flush()
            if registry.shutdown_requested.is_set():
                threading.Thread(target=server.shutdown, daemon=True).start()

    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True

    with Server(socket_path, Handler) as server:
        os.chmod(socket_path, 0o777)
        ready.touch(exist_ok=False)
        try:
            server.serve_forever(poll_interval=0.1)
        finally:
            ready.unlink(missing_ok=True)
            Path(socket_path).unlink(missing_ok=True)
            registry.control({"operation": "close"})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--work", type=Path, default=Path("/work"))
    parser.add_argument("--socket", dest="socket_path", default="/run/registry.sock")
    parser.add_argument("--ready", type=Path, default=Path("/run/ready"))
    parser.add_argument("--fixture")
    parser.add_argument("--run-id")
    serve(**vars(parser.parse_args()))


if __name__ == "__main__":
    main()
