"""Identity decisions are tested independently from Docker and process timing."""

import json
import os
import signal
import socket
import subprocess
import sys
import threading
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock

import pytest

from evidencegraph.lab.runtime import client, server
from evidencegraph.lab.runtime.server import (
    FIRST_UID,
    DispatchRejected,
    PeerIdentity,
    ProcessIdentity,
    Registry,
)

PEER = PeerIdentity(
    pid=200, uid=FIRST_UID + 1, gid=FIRST_UID + 1, sid=100, start_ticks=25, session_start_ticks=10
)
ROOT_PEER = PeerIdentity(pid=50, uid=0, gid=0, sid=50, start_ticks=1, session_start_ticks=1)


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture
def registry(tmp_path):
    registry = Registry(tmp_path / "truth", fixture="fixture", work=tmp_path / "work")
    registry.invocations[100] = {
        "slot": 1,
        "action": "private-action-B",
        "job_id": "job-example",
        "start_ticks": 10,
        "process_starts": {100: 10, 200: 25},
    }
    return registry


@pytest.mark.parametrize(
    ("peer", "reason"),
    [
        (replace(PEER, uid=1000), "unknown_os_identity"),
        (replace(PEER, start_ticks=None), "incomplete_identity"),
        (replace(PEER, sid=999), "missing_invocation"),
        (replace(PEER, uid=FIRST_UID + 2), "slot_mismatch"),
        (replace(PEER, session_start_ticks=11), "session_start_mismatch"),
        (replace(PEER, start_ticks=26), "process_start_mismatch"),
    ],
)
def test_all_six_identity_rejections_are_recorded_without_mutation(
    registry, monkeypatch, peer, reason
):
    monkeypatch.setattr(
        server, "process_identity", Mock(side_effect=AssertionError("dispatch inspected OS"))
    )
    with pytest.raises(DispatchRejected, match=reason):
        registry.dispatch({"operation": "write", "name": "entry", "payload": "forged"}, peer)
    assert registry.public() == []
    public = rows(registry.root / "runtime-events.jsonl")
    assert public[-1]["kind"] == "rejected"
    assert public[-1]["reason"] == reason
    assert public[-1]["principal_id"].startswith("principal-")
    assert not {"uid", "gid", "pid", "sid", "slot", "action", "writer"} & public[-1].keys()
    audit = rows(registry.root / "audit.jsonl")[-1]
    assert audit["peer"]["pid"] == peer.pid
    assert audit["principal_id"] == public[-1]["principal_id"]


def test_root_control_accepts_without_invocation_and_returns_local_clock(registry, monkeypatch):
    monkeypatch.setattr(server, "effective_limits", lambda: {"cgroup_version": 2})
    response = registry.dispatch({"operation": "startup"}, ROOT_PEER)
    assert response["ready"]
    assert response["limits"] == {"cgroup_version": 2}
    assert response["clock"]["wall_ns"] > 0
    assert response["clock"]["monotonic_ns"] > 0
    assert "+00:00" in response["clock"]["wall_ts"]
    write = registry.dispatch(
        {
            "operation": "invoke",
            "slot": 0,
            "action": "root-action",
            "request": {"operation": "write", "name": "entry", "payload": "value"},
        },
        ROOT_PEER,
    )
    assert write["accepted"]
    assert registry.collector.host_truth()[0].writer_slot == 0


def test_bound_job_accepts_and_preserves_receipt_and_private_identity(registry, monkeypatch):
    monkeypatch.setattr(
        server, "process_identity", Mock(side_effect=AssertionError("dispatch inspected OS"))
    )
    response = registry.dispatch({"operation": "write", "name": "entry", "payload": "value"}, PEER)
    assert response["accepted"]
    mutation = registry.collector.host_truth()[0]
    assert mutation.background
    assert mutation.writer_slot == 1
    ledger = json.loads((registry.root / "public-ledger.json").read_text())
    assert ledger == registry.public()
    assert (
        ledger[0]["receipt_token_sha256"] == sha256(response["receipt_token"].encode()).hexdigest()
    )
    contexts = json.loads((registry.root / "contexts.json").read_text())
    assert contexts["1"]["job_id"] == "job-example"
    assert contexts["1"]["slot"] == 1
    principal = rows(registry.root / "runtime-events.jsonl")[-1]["principal_id"]
    assert contexts["1"]["principal_id"] == principal
    private = json.loads((registry.root / "principal-map.json").read_text())
    assert private[0]["uid"] == PEER.uid and private[0]["principal_id"] == principal
    assert (registry.root.stat().st_mode & 0o777) == 0o700


def test_first_seen_descendant_is_bound_then_pid_reuse_is_rejected(registry):
    descendant = replace(PEER, pid=300, start_ticks=40)
    registry.dispatch({"operation": "list"}, descendant)
    assert registry.invocations[100]["process_starts"][300] == 40
    with pytest.raises(DispatchRejected, match="process_start_mismatch"):
        registry.dispatch({"operation": "list"}, replace(descendant, start_ticks=41))


def test_public_principals_are_opaque_stable_and_scoped_to_run(registry, tmp_path):
    registry.dispatch({"operation": "list"}, PEER)
    registry.dispatch({"operation": "list"}, PEER)
    first, second = rows(registry.root / "runtime-events.jsonl")
    assert first["principal_id"] == second["principal_id"]
    other = Registry(tmp_path / "other-truth")
    other.invocations[100] = registry.invocations[100]
    other.dispatch({"operation": "list"}, PEER)
    third = rows(other.root / "runtime-events.jsonl")[0]
    assert third["principal_id"] != first["principal_id"]
    assert third["run_id"] != first["run_id"]
    for row in (first, second, third):
        assert "private-action-B" not in json.dumps(row)
        assert (
            not {"pid", "uid", "gid", "sid", "slot", "action", "writer", "start_ticks"} & row.keys()
        )


def test_principal_and_snapshot_persistence_failures_close_registry(registry, monkeypatch):
    monkeypatch.setattr(server, "atomic_json", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        registry.dispatch({"operation": "list"}, PEER)
    assert registry.closed
    assert registry.public() == []


def test_snapshot_failure_cannot_acknowledge_an_unexported_write(registry, monkeypatch):
    monkeypatch.setattr(server, "atomic_json", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        registry.operate(
            {"operation": "write", "name": "entry", "payload": "value"},
            {"slot": 0, "action": "write", "job_id": None},
        )
    assert registry.closed
    assert len(registry.collector.host_truth()) == 1
    with pytest.raises(ValueError, match="closed"):
        registry.operate({"operation": "list"}, {"slot": 0})


def test_process_stat_handles_spaces_and_parentheses(tmp_path):
    directory = tmp_path / "123"
    directory.mkdir()
    fields = ["S", "1", "123", "123"] + ["0"] * 15 + ["456"]
    (directory / "stat").write_text("123 (name with ) embedded) " + " ".join(fields))
    assert server.process_identity(123, proc_root=tmp_path) == ProcessIdentity(123, 123, 456)


@pytest.mark.skipif(sys.platform != "linux", reason="SO_PEERCRED is Linux-only")
def test_peer_identity_uses_kernel_credentials_and_start_information():
    left, right = socket.socketpair()
    with left, right:
        identity = server.peer_identity(left)
    assert identity.pid == os.getpid()
    assert identity.uid == os.getuid()
    assert identity.gid == os.getgid()
    assert identity.sid == os.getsid(0)
    assert identity.start_ticks == server.process_identity(os.getpid()).start_ticks
    assert identity.session_start_ticks == server.process_identity(os.getsid(0)).start_ticks


@pytest.mark.skipif(sys.platform != "linux", reason="SO_PEERCRED is Linux-only")
def test_disappearing_peer_yields_recordable_incomplete_identity(monkeypatch):
    monkeypatch.setattr(server, "process_identity", Mock(side_effect=ProcessLookupError))
    left, right = socket.socketpair()
    with left, right:
        peer = server.peer_identity(left)
    assert peer.pid == os.getpid()
    assert peer.start_ticks is None and peer.session_start_ticks is None


def test_effective_limits_reads_cgroup_v2_files(tmp_path):
    for name, value in {
        "cgroup.controllers": "cpu memory pids\n",
        "memory.max": "536870912\n",
        "cpu.max": "200000 100000\n",
        "pids.max": "64\n",
    }.items():
        (tmp_path / name).write_text(value)
    assert server.effective_limits(tmp_path) == {
        "cgroup_version": 2,
        "memory_max": "536870912",
        "cpu_max": "200000 100000",
        "pids_max": "64",
    }


def fake_spawn(registry, monkeypatch, *, timeout=False, during_wait=None):
    class Process:
        pid = 400
        returncode = None
        waited = False

        def wait(self, timeout=None):
            if not self.waited:
                self.waited = True
                if during_wait:
                    during_wait()
                if timeout is not None and timed_out:
                    raise subprocess.TimeoutExpired("job", timeout)
            self.returncode = -9 if timed_out else 0
            return self.returncode

    timed_out = timeout
    process = Process()
    popen = Mock(return_value=process)
    killpg = Mock()
    monkeypatch.setattr(server.subprocess, "Popen", popen)
    monkeypatch.setattr(server.os, "killpg", killpg)
    monkeypatch.setattr(server, "process_identity", lambda pid: ProcessIdentity(pid, pid, 50))
    return popen, killpg


def test_spawn_uses_immutable_version_and_drops_privileges(registry, monkeypatch):
    popen, _ = fake_spawn(registry, monkeypatch)
    context = {"slot": 1, "action": "publish", "job_id": None}
    published = registry.operate(
        {"operation": "write", "name": "probe.py", "payload": "print('selected')"}, context
    )
    registry.operate(
        {"operation": "write", "name": "probe.py", "payload": "print('replacement')"}, context
    )
    launched = registry.control(
        {"operation": "start_job", "slot": 1, "action": "launch", "version": published["sha256"]}
    )
    assert launched["sha256"] == published["sha256"]
    assert popen.call_args.args[0][-1] == "print('selected')"
    options = popen.call_args.kwargs
    assert options["user"] == options["group"] == FIRST_UID + 1
    assert options["extra_groups"] == []
    assert options["start_new_session"]
    assert options["cwd"] == registry.work / "1"
    assert "HOME" not in options["env"]
    assert registry.invocations[400]["start_ticks"] == 50
    assert registry.invocations[400]["process_starts"] == {400: 50}
    assert registry.invocations[400]["job_id"] == launched["job_id"]


def test_unpublished_version_cannot_start_job(registry, monkeypatch):
    popen, _ = fake_spawn(registry, monkeypatch)
    with pytest.raises(ValueError, match="published immutable"):
        registry.control(
            {"operation": "start_job", "slot": 1, "action": "launch", "version": "a" * 64}
        )
    popen.assert_not_called()


def test_drain_deadline_kills_process_group_and_revokes_binding(registry, monkeypatch):
    _, killpg = fake_spawn(registry, monkeypatch, timeout=True)
    launched = registry.spawn(1, "pass", "launch", job=True)
    response = registry.control({"operation": "drain", "seconds": 0})
    job = response["jobs"][0]
    assert job["job_id"] == launched["job_id"]
    assert job["timed_out"] and job["exit_code"] == -9
    killpg.assert_called_once_with(400, signal.SIGKILL)
    assert 400 not in registry.invocations
    assert registry.control({"operation": "drain", "seconds": 0}) == response


def test_drain_releases_lock_for_background_polling(registry, monkeypatch):
    completed = threading.Event()

    def during_wait():
        def background_request():
            registry.operate({"operation": "list"}, {"slot": 1})
            completed.set()

        thread = threading.Thread(target=background_request, daemon=True)
        thread.start()
        assert completed.wait(2), "drain blocked the background socket operation"
        thread.join()

    fake_spawn(registry, monkeypatch, during_wait=during_wait)
    registry.spawn(1, "pass", "launch", job=True)
    registry.control({"operation": "drain", "seconds": 5})
    assert completed.is_set()


def test_shutdown_drains_flushes_and_signals_server_exit(registry, monkeypatch):
    fake_spawn(registry, monkeypatch, timeout=True)
    registry.spawn(1, "pass", "launch", job=True)
    response = registry.dispatch({"operation": "shutdown"}, ROOT_PEER)
    assert response["closed"] and response["clock"]["wall_ns"]
    assert registry.shutdown_requested.is_set()
    assert registry.closed
    assert json.loads((registry.root / "public-ledger.json").read_text()) == registry.public()
    assert any(row["kind"] == "stop" for row in rows(registry.root / "mutations.jsonl"))


def test_client_cli_reads_stdin_and_preserves_server_error(tmp_path):
    socket_path = tmp_path / "socket"
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(socket_path))
    listener.listen(1)
    captured = []

    def reply():
        with listener:
            connection, _ = listener.accept()
            with connection, connection.makefile("rb") as stream:
                captured.append(json.loads(stream.readline()))
                connection.sendall(b'{"error":"rejected","clock":{"wall_ns":123}}\n')

    thread = threading.Thread(target=reply, daemon=True)
    thread.start()
    result = subprocess.run(
        [sys.executable, str(Path(client.__file__))],
        input='{"operation":"startup"}',
        text=True,
        capture_output=True,
        env={**os.environ, "EG_REGISTRY_SOCKET": str(socket_path)},
        timeout=10,
    )
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result.returncode == 1
    assert captured == [{"operation": "startup"}]
    assert json.loads(result.stdout) == {"error": "rejected", "clock": {"wall_ns": 123}}
