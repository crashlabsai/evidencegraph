"""Docker controller contracts through an executable shim in the default suite."""

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from inspect_ai.log import read_eval_log
from typer.testing import CliRunner

from evidencegraph.cli import app
from evidencegraph.lab.docker import IMAGE, collect_docker
from evidencegraph.lab.stage import ScenarioRecorder
from evidencegraph.provenance import sha256_file

PINNED_IMAGE = (
    "python:3.12-slim@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17"
)


@dataclass
class DockerShim:
    binary: Path
    root: Path

    def calls(self) -> list[dict]:
        path = self.root / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def state(self) -> dict:
        return json.loads((self.root / "state.json").read_text())


@pytest.fixture
def docker_shim(tmp_path, monkeypatch):
    root = tmp_path / "shim-state"
    binary = tmp_path / "fake-docker"
    source = Path(__file__).with_name("fake_docker.py")
    binary.write_text(
        f"#!{sys.executable}\nimport runpy\nrunpy.run_path({str(source)!r}, run_name='__main__')\n"
    )
    binary.chmod(0o755)
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(root))
    monkeypatch.setenv("FAKE_DOCKER_MODE", "success")
    return DockerShim(binary=binary, root=root)


def load_report(out: Path, *, status: str, failing_step: str | None) -> dict:
    report = json.loads((out / "collection.json").read_text())
    assert report["status"] == status, report
    assert report["failing_step"] == failing_step, report
    assert json.loads((out / "public" / "collection.json").read_text()) == report
    population = out / "public" / "population.json"
    if population.exists():
        assert json.loads(population.read_text())["complete"] is (status == "ok")
    return report


def assert_removed(shim: DockerShim, report: dict) -> None:
    assert report["retained_resources"] == {"containers": [], "volumes": []}
    assert report["cleanup_commands"] == []
    state = shim.state()
    assert state["containers"] == []
    assert state["volumes"] == []
    calls = shim.calls()
    exports = [i for i, row in enumerate(calls) if row["args"][0] == "cp"]
    removals = [i for i, row in enumerate(calls) if row["args"][:2] == ["volume", "rm"]]
    assert exports and removals and max(exports) < min(removals)
    container_removals = [i for i, row in enumerate(calls) if row["args"][0] == "rm"]
    assert container_removals and max(exports) < min(container_removals) < min(removals)
    assert all(
        "--force" in calls[i]["args"] or "-f" in calls[i]["args"] for i in container_removals
    )
    assert_stopped_before_export(shim)


def assert_stopped_before_export(shim: DockerShim) -> None:
    exports = [
        row
        for row in shim.calls()
        if row["args"][0] == "cp" or row["args"][0] == "run" and "--rm" in row["args"]
    ]
    assert exports and all(row["stopped"] for row in exports)


def option(args: list[str], name: str) -> str | None:
    if name in args:
        return args[args.index(name) + 1]
    return next((arg.split("=", 1)[1] for arg in args if arg.startswith(name + "=")), None)


def assert_request_brackets(out: Path, shim: DockerShim) -> list[dict]:
    rows = [
        json.loads(line)
        for line in (out / "private" / "control-requests.jsonl").read_text().splitlines()
    ]
    requests = [row for row in shim.calls() if "request" in row]
    assert len(rows) == len(requests)
    assert len({row["request_id"] for row in rows}) == len(rows)
    for row, call in zip(rows, requests, strict=True):
        assert row["operation"] == call["request"]["operation"]
        assert row["request"] == call["request"]
        assert row["host_send_ns"] <= call["at_ns"] <= row["host_receive_ns"]
        assert row["host_send_monotonic_ns"] <= row["host_receive_monotonic_ns"]
        assert row["bracket_width_seconds"] == pytest.approx(
            (row["host_receive_ns"] - row["host_send_ns"]) / 1_000_000_000
        )
        if row["ok"]:
            assert row["container_clock"]["wall_ts"]
            assert row["container_clock"]["wall_ns"] > 0
            assert row["container_clock"]["monotonic_ns"] > 0
        else:
            assert row["error"]
    return rows


def run_cli(out: Path, shim: DockerShim, *, timeout: int = 20):
    return CliRunner().invoke(
        app,
        [
            "lab",
            "collect-docker",
            str(out),
            "--seed",
            "7",
            "--timeout-seconds",
            str(timeout),
            "--docker-binary",
            str(shim.binary),
        ],
    )


def test_collection_runs_pinned_isolated_container_and_exports_before_cleanup(
    tmp_path, docker_shim
):
    out = tmp_path / "collection"
    result = collect_docker(out, seed=7, timeout_seconds=20, docker_binary=str(docker_shim.binary))
    report = load_report(out, status="ok", failing_step=None)
    assert result == report
    assert IMAGE == PINNED_IMAGE
    assert_removed(docker_shim, report)
    assert all(row["ok"] for row in assert_request_brackets(out, docker_shim))

    calls = docker_shim.calls()
    launch = next(row["args"] for row in calls if row["args"][0] == "run")
    assert "--detach" in launch or "-d" in launch
    assert PINNED_IMAGE in launch
    assert option(launch, "--network") == "none"
    assert "--read-only" in launch
    assert option(launch, "--memory") in {"512m", "512M", "536870912"}
    assert option(launch, "--cpus") in {"2", "2.0"}
    assert option(launch, "--pids-limit") == "64"
    assert option(launch, "--cap-drop") == "ALL"
    assert "--privileged" not in launch
    assert not any("docker.sock" in argument for argument in launch)
    assert any(
        "/app" in argument and (":ro" in argument or "readonly" in argument) for argument in launch
    )

    requests = [row for row in calls if "request" in row]
    operations = [row["request"]["operation"] for row in requests]
    assert operations[0] == "startup"
    assert operations[-1] == "shutdown"
    assert "start_job" in operations and "refresh" in operations and "drain" in operations
    for row in requests:
        assert row["args"][0] == "exec"
        assert "-i" in row["args"]
        assert option(row["args"], "--user") == "0"
        assert row["args"][-2:] == ["python", "/app/client.py"]

    public, private = out / "public", out / "private"
    assert private.stat().st_mode & 0o777 == 0o700
    assert (private / "host" / "audit.jsonl").is_file()
    assert (private / "host" / "mutations.jsonl").is_file()
    assert len(list((public / "transcripts").glob("*.eval"))) == 4
    assert (public / "ledger.jsonl").read_text()
    assert not (public / "audit.jsonl").exists()
    assert not (public / "mutations.jsonl").exists()
    log = read_eval_log(private / "B.eval")
    assert log.samples
    launch_event = next(
        event
        for event in log.samples[0].events
        if event.event == "tool" and event.function == "start_job"
    )
    assert isinstance(launch_event.result, str)
    receipt = json.loads(launch_event.result)
    assert receipt["started"] is True
    assert receipt["job_id"] == "shim-job-1"
    assert receipt["sha256"] == docker_shim.state()["job"]["version"]
    manifest = json.loads((public / "manifest.json").read_text())
    for relative, details in manifest["files"].items():
        assert sha256_file(public / relative) == details["sha256"]


def test_missing_docker_cli_returns_failed_preflight_report(tmp_path):
    out = tmp_path / "collection"
    missing = tmp_path / "docker-does-not-exist"
    result = CliRunner().invoke(
        app, ["lab", "collect-docker", str(out), "--docker-binary", str(missing)]
    )
    assert result.exit_code == 1, result.output
    report = load_report(out, status="failed", failing_step="preflight")
    assert report["retained_resources"] == {"containers": [], "volumes": []}
    assert report["cleanup_commands"] == []
    assert "docker" in result.output.lower()


def test_missing_image_prints_exact_pull_without_creating_resources(
    tmp_path, docker_shim, monkeypatch
):
    monkeypatch.setenv("FAKE_DOCKER_MODE", "missing-image")
    out = tmp_path / "collection"
    result = run_cli(out, docker_shim)
    assert result.exit_code == 1, result.output
    report = load_report(out, status="failed", failing_step="image")
    assert f"docker pull {PINNED_IMAGE}" in result.output
    assert report["retained_resources"] == {"containers": [], "volumes": []}
    assert not any(row["args"][0] in {"run", "volume"} for row in docker_shim.calls())


def test_cgroup_v1_is_rejected_without_creating_resources(tmp_path, docker_shim, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_MODE", "cgroup-v1")
    out = tmp_path / "collection"
    result = run_cli(out, docker_shim)
    assert result.exit_code == 1, result.output
    report = load_report(out, status="failed", failing_step="preflight")
    assert report["retained_resources"] == {"containers": [], "volumes": []}
    assert "CgroupVersion 2" in result.output
    assert len(docker_shim.calls()) == 1


def test_never_ready_exports_logs_and_removes_resources(tmp_path, docker_shim, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_MODE", "never-ready")
    out = tmp_path / "collection"
    result = run_cli(out, docker_shim, timeout=1)
    assert result.exit_code == 1, result.output
    report = load_report(out, status="failed", failing_step="start")
    assert_removed(docker_shim, report)
    assert "fake supervisor log" in (out / "private" / "docker.log").read_text()
    assert not any("request" in row for row in docker_shim.calls())


def test_killed_supervisor_exports_stopped_container_truth(tmp_path, docker_shim, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_MODE", "killed-supervisor")
    out = tmp_path / "collection"
    result = run_cli(out, docker_shim)
    assert result.exit_code == 1, result.output
    report = load_report(out, status="failed", failing_step="scenario")
    assert_removed(docker_shim, report)
    assert docker_shim.state()["stopped"]
    assert (out / "private" / "host" / "mutations.jsonl").read_text()
    assert (out / "public" / "ledger.jsonl").read_text()
    assert not any("--rm" in row["args"] for row in docker_shim.calls())
    assert any(not row["ok"] for row in assert_request_brackets(out, docker_shim))


def test_volume_fallback_exports_after_docker_cp_fails(tmp_path, docker_shim, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_MODE", "fallback-export")
    out = tmp_path / "collection"
    result = run_cli(out, docker_shim)
    assert result.exit_code == 0, result.output
    report = load_report(out, status="ok", failing_step=None)
    assert_removed(docker_shim, report)
    calls = docker_shim.calls()
    fallback = next(
        row["args"] for row in calls if row["args"][0] == "run" and "--rm" in row["args"]
    )
    assert PINNED_IMAGE in fallback
    assert option(fallback, "--network") == "none"
    assert "--read-only" in fallback
    assert option(fallback, "--name")
    mount = option(fallback, "--mount")
    assert mount and "type=volume" in mount and "dst=/truth" in mount and "readonly" in mount
    assert (out / "private" / "host" / "public-ledger.json").is_file()
    assert (out / "public" / "ledger.jsonl").read_text()


def test_both_exports_fail_retains_only_volume_and_prints_cleanup(
    tmp_path, docker_shim, monkeypatch
):
    monkeypatch.setenv("FAKE_DOCKER_MODE", "export-failure")
    out = tmp_path / "collection"
    result = run_cli(out, docker_shim)
    assert result.exit_code == 1, result.output
    report = load_report(out, status="failed", failing_step="export")
    assert report["retained_resources"]["containers"] == []
    retained = report["retained_resources"]["volumes"]
    assert len(retained) == 1
    command = f"docker volume rm {retained[0]}"
    assert command in report["cleanup_commands"]
    assert command in result.output
    assert docker_shim.state()["containers"] == []
    assert docker_shim.state()["volumes"] == retained
    assert not any(row["args"][:2] == ["volume", "rm"] for row in docker_shim.calls())
    assert any(row["args"][0] == "cp" for row in docker_shim.calls())
    assert any(row["args"][0] == "run" and "--rm" in row["args"] for row in docker_shim.calls())
    assert_stopped_before_export(docker_shim)


@pytest.mark.parametrize("mode", ["incomplete-ledger", "incomplete-object", "corrupt-object"])
def test_invalid_copy_uses_complete_volume_fallback(tmp_path, docker_shim, monkeypatch, mode):
    monkeypatch.setenv("FAKE_DOCKER_MODE", mode)
    out = tmp_path / "collection"
    result = run_cli(out, docker_shim)
    assert result.exit_code == 0, result.output
    report = load_report(out, status="ok", failing_step=None)
    assert_removed(docker_shim, report)
    assert any(row["args"][0] == "run" and "--rm" in row["args"] for row in docker_shim.calls())
    host = out / "private" / "host"
    rows = json.loads((host / "public-ledger.json").read_text())
    assert rows
    for row in rows:
        assert sha256_file(host / "objects" / row["sha256"]) == row["sha256"]


def test_both_incomplete_exports_retain_volume_even_with_zero_exit_status(
    tmp_path, docker_shim, monkeypatch
):
    monkeypatch.setenv("FAKE_DOCKER_MODE", "incomplete-both")
    out = tmp_path / "collection"
    result = run_cli(out, docker_shim)
    assert result.exit_code == 1, result.output
    report = load_report(out, status="failed", failing_step="export")
    assert report["retained_resources"]["containers"] == []
    volumes = report["retained_resources"]["volumes"]
    assert len(volumes) == 1
    assert docker_shim.state()["volumes"] == volumes
    assert f"docker volume rm {volumes[0]}" in result.output
    assert not any(row["args"][:2] == ["volume", "rm"] for row in docker_shim.calls())
    assert_stopped_before_export(docker_shim)


def test_transcript_failure_marks_published_population_incomplete(
    tmp_path, docker_shim, monkeypatch
):
    def fail_transcripts(*args, **kwargs):
        raise OSError("simulated transcript write failure")

    monkeypatch.setattr(ScenarioRecorder, "write_transcripts", fail_transcripts)
    out = tmp_path / "collection"
    result = collect_docker(out, timeout_seconds=20, docker_binary=str(docker_shim.binary))
    report = load_report(out, status="failed", failing_step="publish")
    assert result == report
    assert (out / "public" / "population.json").is_file()
    assert_removed(docker_shim, report)


def assert_drained_before_export(shim: DockerShim) -> None:
    calls = shim.calls()
    drains = [
        i for i, row in enumerate(calls) if row.get("request", {}).get("operation") == "drain"
    ]
    exports = [i for i, row in enumerate(calls) if row["args"][0] == "cp"]
    assert drains and exports and max(drains) < min(exports)


def test_whole_run_deadline_drains_before_export_and_cleanup(tmp_path, docker_shim, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_MODE", "deadline")
    out = tmp_path / "collection"
    result = run_cli(out, docker_shim, timeout=3)
    assert result.exit_code == 1, result.output
    report = load_report(out, status="failed", failing_step="deadline")
    assert_drained_before_export(docker_shim)
    assert_removed(docker_shim, report)
    assert any(not row["ok"] for row in assert_request_brackets(out, docker_shim))


@pytest.mark.parametrize(
    ("mode", "failing_step"),
    [("shutdown-timeout", "deadline"), ("shutdown-wait-timeout", "shutdown")],
)
def test_shutdown_timeout_kills_supervisor_before_export(
    tmp_path, docker_shim, monkeypatch, mode, failing_step
):
    monkeypatch.setenv("FAKE_DOCKER_MODE", mode)
    out = tmp_path / "collection"
    result = run_cli(out, docker_shim, timeout=3 if mode == "shutdown-timeout" else 20)
    assert result.exit_code == 1, result.output
    report = load_report(out, status="failed", failing_step=failing_step)
    assert_drained_before_export(docker_shim)
    assert_removed(docker_shim, report)
    calls = docker_shim.calls()
    kill = next(i for i, row in enumerate(calls) if row["args"][0] == "kill")
    export = next(i for i, row in enumerate(calls) if row["args"][0] == "cp")
    assert not calls[kill]["stopped"]
    assert kill < export
    assert any(row["args"][0] == "wait" for row in calls[kill + 1 : export])


def test_interrupt_is_reported_after_drain_export_and_cleanup(tmp_path, docker_shim, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_MODE", "interrupt")
    out = tmp_path / "collection"
    # Send SIGINT only to an isolated CLI process; pytest never receives the signal.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "evidencegraph.cli",
            "lab",
            "collect-docker",
            str(out),
            "--timeout-seconds",
            "20",
            "--docker-binary",
            str(docker_shim.binary),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=os.environ.copy(),
    )
    assert result.returncode == 1, result.stdout + result.stderr
    report = load_report(out, status="failed", failing_step="interrupt")
    assert docker_shim.state()["interrupted"]
    assert_drained_before_export(docker_shim)
    assert_removed(docker_shim, report)
    assert any(not row["ok"] for row in assert_request_brackets(out, docker_shim))


@pytest.mark.parametrize(
    "mode", ["bad-limits", "bad-cpu-limits", "bad-pids-limits", "bad-cgroup-limits"]
)
def test_effective_limits_are_verified_before_scenario(tmp_path, docker_shim, monkeypatch, mode):
    monkeypatch.setenv("FAKE_DOCKER_MODE", mode)
    out = tmp_path / "collection"
    result = run_cli(out, docker_shim)
    assert result.exit_code == 1, result.output
    report = load_report(out, status="failed", failing_step="limits")
    assert_removed(docker_shim, report)
    assert not any(
        row.get("request", {}).get("operation") == "invoke" for row in docker_shim.calls()
    )


def test_existing_output_is_untouched_and_no_docker_command_runs(tmp_path, docker_shim):
    out = tmp_path / "collection"
    out.mkdir()
    marker = out / "existing.txt"
    marker.write_bytes(b"leave this output alone\n")
    with pytest.raises(ValueError, match="already exists"):
        collect_docker(out, docker_binary=str(docker_shim.binary))
    assert marker.read_bytes() == b"leave this output alone\n"
    assert list(out.iterdir()) == [marker]
    assert docker_shim.calls() == []


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_deadline_is_rejected_before_output_creation(tmp_path, docker_shim, timeout):
    out = tmp_path / "collection"
    with pytest.raises(ValueError, match="timeout"):
        collect_docker(out, timeout_seconds=timeout, docker_binary=str(docker_shim.binary))
    assert not out.exists()
    assert docker_shim.calls() == []
