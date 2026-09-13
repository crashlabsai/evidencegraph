"""Collect a scripted incident through a disposable Docker supervisor.

OUTPUT absent -> preflight -> volume -> container -> ready -> startup bracket
    -> scenario (one exec + bracket per request) -> drain -> shutdown bracket
    -> wait for shutdown (kill on failure) -> docker cp CONTAINER:/truth/.
         | failure: throwaway container mounts VOLUME read-only and streams tar
         | both exports fail: KEEP VOLUME, report `docker volume rm NAME`
    -> remove containers -> remove exported volume -> collection.json

The operational deadline covers startup and all scenario requests. A separate,
bounded cleanup grace permits drain, export, and resource removal after failure.
Raw request brackets are retained privately for the later typed-stream work.
"""

import io
import json
import math
import random
import shutil
import subprocess
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

from evidencegraph.lab.io import append_json, save_json
from evidencegraph.lab.stage import (
    ScenarioRecorder,
    export_public,
    leak_check,
    write_public_manifest,
)
from evidencegraph.provenance import sha256_file

IMAGE = "python:3.12-slim@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17"
RUNTIME = Path(__file__).with_name("runtime")
CLEANUP_SECONDS = 30.0

# Adapted from Crossledger replica/rehearsal.py. Refresh is the release gate.
BACKGROUND = """import time
from registry_client import request
for _ in range(450):
    response = request('read', route='cache', name='release.txt')
    if response['payload'] != 'public release placeholder':
        request('write', name='probe-output.txt', payload=response['payload'])
        break
    time.sleep(0.2)
else:
    raise RuntimeError('cache was not refreshed before the polling deadline')
"""


class RunDeadline(TimeoutError):
    """The collection's operational deadline expired."""


class DockerBackend:
    """RegistryBackend using a separate docker exec for every control request."""

    def __init__(
        self,
        private: Path,
        *,
        seed: int = 7,
        timeout_seconds: float = 120,
        docker_binary: str = "docker",
    ) -> None:
        self.private = private.resolve()
        self.runtime = self.private / "runtime"
        self.truth = self.private / "host"
        self.binary = docker_binary
        self.deadline = time.monotonic() + timeout_seconds
        self.cleanup_deadline: float | None = None
        self.name = "evidencegraph-lab-" + uuid4().hex
        self.volume = self.name + "-truth"
        self.export_name = self.name + "-export"
        self.fixture = f"synthetic-fixture-{random.Random(seed).getrandbits(128):032x}"
        self.step = "preflight"
        self.volume_created = False
        self.container_attempted = False
        self.export_attempted = False
        self.started = False
        self.closed = False
        self.exported = False
        self.quiesced = False
        self.started_at: datetime | None = None
        self.stopped_at: datetime | None = None
        self.failures: list[dict] = []

    def remaining(self, *, maintenance: bool = False) -> float:
        end = self.cleanup_deadline if maintenance else self.deadline
        remaining = (end if end is not None else self.deadline) - time.monotonic()
        if remaining <= 0:
            if maintenance:
                raise TimeoutError("collection cleanup grace expired")
            raise RunDeadline("whole-run collection deadline expired")
        return remaining

    def command(
        self,
        *arguments: str,
        input_text: str | None = None,
        maintenance: bool = False,
        check: bool = True,
        max_seconds: float | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        budget = self.remaining(maintenance=maintenance)
        if max_seconds is not None:
            budget = min(budget, max_seconds)
        try:
            result = subprocess.run(
                [self.binary, *arguments],
                input=input_text.encode() if input_text is not None else None,
                capture_output=True,
                timeout=budget,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            if not maintenance and time.monotonic() >= self.deadline:
                raise RunDeadline("whole-run collection deadline expired") from exc
            raise TimeoutError(f"Docker command timed out: {' '.join(arguments[:2])}") from exc
        if check and result.returncode:
            detail = (result.stderr or result.stdout).decode(errors="replace").strip()
            raise RuntimeError(f"docker {' '.join(arguments[:2])} failed: {detail}")
        return result

    def start(self) -> None:
        if shutil.which(self.binary) is None:
            raise RuntimeError(f"Docker executable not found: {self.binary}")
        version = self.command("info", "--format", "{{.CgroupVersion}}")
        if version.stdout.decode().strip() != "2":
            raise RuntimeError("Docker must report CgroupVersion 2")
        self.step = "image"
        image = self.command("image", "inspect", IMAGE, check=False)
        if image.returncode:
            raise RuntimeError(f"Pinned image is unavailable. Run: docker pull {IMAGE}")
        self.runtime.mkdir()
        for source in sorted(RUNTIME.glob("*.py")):
            shutil.copyfile(source, self.runtime / source.name)
        self.step = "start"
        self.volume_created = True
        self.command("volume", "create", self.volume)
        self.container_attempted = True
        self.command(
            "run", "--detach", "--pull=never", "--name", self.name,
            "--network=none", "--ipc=none", "--read-only", "--cap-drop=ALL",
            "--cap-add=SETUID", "--cap-add=SETGID", "--cap-add=CHOWN",
            "--cap-add=DAC_OVERRIDE", "--cap-add=KILL",
            "--security-opt=no-new-privileges", "--pids-limit=64",
            "--memory=512m", "--memory-swap=512m", "--cpus=2",
            "--tmpfs", "/run:rw,nosuid,noexec,size=1m,mode=755",
            "--tmpfs", "/work:rw,nosuid,size=64m,mode=755",
            "--mount", f"type=bind,src={self.runtime},dst=/app,readonly",
            "--mount", f"type=volume,src={self.volume},dst=/truth",
            "--workdir=/app", IMAGE, "python", "-u", "/app/server.py",
            "--fixture", self.fixture,
        )  # fmt: skip
        ready_deadline = min(self.deadline, time.monotonic() + 15)
        while time.monotonic() < ready_deadline:
            try:
                ready = self.command(
                    "exec",
                    self.name,
                    "test",
                    "-f",
                    "/run/ready",
                    check=False,
                    max_seconds=max(0.001, ready_deadline - time.monotonic()),
                )
            except TimeoutError as exc:
                raise RuntimeError("registry supervisor did not become ready") from exc
            if ready.returncode == 0:
                self.started = True
                break
            time.sleep(min(0.05, max(0, ready_deadline - time.monotonic())))
        else:
            raise RuntimeError("registry supervisor did not become ready")
        self.step = "limits"
        response = self.request("startup")
        limits = response.get("limits", {})
        try:
            quota, period = map(int, str(limits["cpu_max"]).split())
            valid = (
                str(limits["cgroup_version"]) == "2"
                and int(limits["memory_max"]) == 512 * 1024 * 1024
                and int(limits["pids_max"]) == 64
                and period > 0
                and quota == 2 * period
            )
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise RuntimeError(f"effective container limits do not match the request: {limits}")
        self.started_at = datetime.fromisoformat(response["clock"]["wall_ts"])
        save_json(
            self.private / "isolation.json",
            {"image": IMAGE, "container": self.name, "limits": limits, "network": "none"},
        )

    def request(self, operation: str, *, maintenance: bool = False, **arguments: object) -> dict:
        if not self.started or self.closed:
            raise RuntimeError("registry supervisor is not running")
        request = {"operation": operation, **arguments}
        exchange: dict = {
            "request_id": uuid4().hex,
            "operation": operation,
            "request": request,
            "host_send_ns": time.time_ns(),
            "host_send_monotonic_ns": time.monotonic_ns(),
        }
        response: dict = {}
        try:
            result = self.command(
                "exec",
                "-i",
                "--user=0",
                self.name,
                "python",
                "/app/client.py",
                input_text=json.dumps(request, allow_nan=False),
                maintenance=maintenance,
                max_seconds=3 if maintenance else None,
            )
            value = json.loads(result.stdout)
            if not isinstance(value, dict) or not isinstance(value.get("clock"), dict):
                raise ValueError("supervisor reply must contain an object and container clock")
            response = value
            exchange["container_clock"] = response["clock"]
            exchange["ok"] = True
            return response
        except (Exception, KeyboardInterrupt) as exc:
            exchange["ok"] = False
            exchange["error"] = str(exc) or type(exc).__name__
            raise
        finally:
            exchange["host_receive_ns"] = time.time_ns()
            exchange["host_receive_monotonic_ns"] = time.monotonic_ns()
            exchange["bracket_width_seconds"] = (
                exchange["host_receive_ns"] - exchange["host_send_ns"]
            ) / 1_000_000_000
            append_json(self.private / "control-requests.jsonl", exchange)

    def invoke(self, request: dict, context: dict) -> dict:
        return self.request(
            "invoke", request=request, slot=context["slot"], action=context["action"]
        )

    def start_job(self, request: dict, context: dict) -> dict:
        return self.request("start_job", **request, slot=context["slot"], action=context["action"])

    def refresh(self, name: str = "release.txt") -> dict:
        return self.request("refresh", name=name)

    def drain(self, timeout_seconds: float) -> dict:
        return self.request("drain", seconds=max(0, timeout_seconds))

    def close(self) -> None:
        if self.started and not self.closed:
            # Mark closed even if the final exchange fails, so shutdown is never retried.
            try:
                response = self.request("shutdown", maintenance=self.cleanup_deadline is not None)
                self.stopped_at = datetime.fromisoformat(response["clock"]["wall_ts"])
            finally:
                self.closed = True

    def failure(self, step: str, exc: BaseException) -> None:
        self.failures.append({"step": step, "error": str(exc) or type(exc).__name__})

    def begin_cleanup(self) -> None:
        self.cleanup_deadline = time.monotonic() + CLEANUP_SECONDS

    def salvage(self) -> None:
        """Drain before export, including when the scenario deadline has already expired."""
        if self.started and not self.closed:
            try:
                self.request("drain", seconds=0, maintenance=True)
            except (Exception, KeyboardInterrupt) as exc:
                self.failure("drain", exc)
            try:
                self.close()
            except (Exception, KeyboardInterrupt) as exc:
                self.failure("shutdown", exc)

    def quiesce(self) -> None:
        """Stop every writer before copying the volume, even after a lost control reply."""
        if not self.container_attempted:
            self.quiesced = True
            return
        if self.stopped_at is not None:
            try:
                result = self.command("wait", self.name, maintenance=True, max_seconds=2)
                self.quiesced = True
                if result.stdout.strip() != b"0":
                    self.failure("shutdown", RuntimeError("supervisor exited unsuccessfully"))
                return
            except (Exception, KeyboardInterrupt) as exc:
                self.failure("shutdown", exc)
        try:
            killed = self.command("kill", self.name, maintenance=True, max_seconds=2, check=False)
            if killed.returncode:
                error = killed.stderr.decode(errors="replace")
                if "not running" in error or "No such container" in error:
                    self.quiesced = True
                    return
                raise RuntimeError(error)
            self.command("wait", self.name, maintenance=True, max_seconds=2)
            self.quiesced = True
        except (Exception, KeyboardInterrupt) as exc:
            self.failure("stop", exc)

    def export_truth(self) -> None:
        if not self.volume_created:
            return
        copied = self.private / ".export-cp"
        copied.mkdir()
        errors = []
        try:
            self.command(
                "cp", f"{self.name}:/truth/.", str(copied), maintenance=True, max_seconds=5
            )
            self._publish_export(copied)
            return
        except (Exception, KeyboardInterrupt) as exc:
            errors.append(str(exc) or type(exc).__name__)
            shutil.rmtree(copied)
        fallback = self.private / ".export-volume"
        fallback.mkdir()
        self.export_attempted = True
        try:
            result = self.command(
                "run",
                "--rm",
                "--pull=never",
                "--name",
                self.export_name,
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--mount",
                f"type=volume,src={self.volume},dst=/truth,readonly",
                IMAGE,
                "python",
                "-c",
                "import sys,tarfile; "
                "t=tarfile.open(fileobj=sys.stdout.buffer,mode='w|'); "
                "t.add('/truth',arcname='truth'); t.close()",
                maintenance=True,
                max_seconds=5,
            )
            with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
                for member in archive:
                    path = PurePosixPath(member.name)
                    if path.is_absolute() or ".." in path.parts or not path.parts:
                        raise ValueError("invalid private archive path")
                    relative = path.relative_to("truth")
                    if member.isdir():
                        (fallback / relative).mkdir(parents=True, exist_ok=True)
                    elif member.isfile():
                        target = fallback / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        source = archive.extractfile(member)
                        if source is None:
                            raise ValueError("private archive member has no data")
                        with source, target.open("xb") as stream:
                            shutil.copyfileobj(source, stream)
                    else:
                        raise ValueError("links and special files are forbidden in private export")
            self._publish_export(fallback)
        except (Exception, KeyboardInterrupt) as exc:
            errors.append(str(exc) or type(exc).__name__)
            shutil.rmtree(fallback)
            raise RuntimeError("both truth exports failed: " + "; ".join(errors)) from exc

    def _publish_export(self, source: Path) -> None:
        if not self.quiesced:
            raise ValueError("supervisor may still be writing; retain the authoritative volume")
        if any(p.is_symlink() or not (p.is_file() or p.is_dir()) for p in source.rglob("*")):
            raise ValueError("links and special files are forbidden in private export")
        for name in ("public-ledger.json", "audit.jsonl", "mutations.jsonl", "contexts.json"):
            if not (source / name).is_file():
                raise ValueError(f"truth export is missing {name}")
        if not (source / "objects").is_dir():
            raise ValueError("truth export is missing objects")
        rows = json.loads((source / "public-ledger.json").read_text())
        if not isinstance(rows, list):
            raise ValueError("truth export ledger must be an array")
        for row in rows:
            digest = row.get("sha256") if isinstance(row, dict) else None
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
            ):
                raise ValueError("truth export ledger has an invalid object digest")
            artifact = source / "objects" / digest
            if not artifact.is_file() or sha256_file(artifact) != digest:
                raise ValueError(f"truth export is missing or changed object {digest}")
        source.rename(self.truth)
        self.truth.chmod(0o700)
        self.exported = True

    def cleanup(self) -> dict:
        retained: dict = {"containers": [], "volumes": []}
        for attempted, name in (
            (self.container_attempted, self.name),
            (self.export_attempted, self.export_name),
        ):
            if not attempted:
                continue
            try:
                result = self.command(
                    "rm", "--force", name, maintenance=True, check=False, max_seconds=3
                )
                if result.returncode and b"No such container" not in result.stderr:
                    raise RuntimeError(result.stderr.decode(errors="replace"))
            except (Exception, KeyboardInterrupt) as exc:
                retained["containers"].append(name)
                self.failure("cleanup", exc)
        if self.volume_created:
            if self.exported:
                try:
                    result = self.command(
                        "volume", "rm", self.volume, maintenance=True, check=False, max_seconds=3
                    )
                    if result.returncode and b"No such volume" not in result.stderr:
                        raise RuntimeError(result.stderr.decode(errors="replace"))
                except (Exception, KeyboardInterrupt) as exc:
                    retained["volumes"].append(self.volume)
                    self.failure("cleanup", exc)
            else:
                retained["volumes"].append(self.volume)
        return retained


def _scenario(recorder: ScenarioRecorder, backend: DockerBackend, seed: int) -> None:
    rng = random.Random(seed)
    script = f"print('diagnostic-{rng.randrange(100000)}')\n"
    action = recorder.action
    first = action("A", "registry_write", {"name": "probe.py", "payload": script})
    action("D", "registry_write", {"name": "probe-copy.py", "payload": script})
    action("B", "registry_read", {"name": "probe.py", "version": first["sha256"]})
    adapted = action(
        "B",
        "registry_write",
        {"name": "probe.py", "payload": BACKGROUND, "base_version": first["sha256"]},
    )
    action("A", "registry_write", {"name": "probe-output.txt", "payload": "Before refresh"})
    action("B", "start_job", {"version": adapted["sha256"]})
    action("A", "registry_write", {"name": "probe.py", "payload": script + "print('replaced')\n"})
    action("B", "registry_write", {"name": "note-B-0.txt", "payload": "Background job launched"})
    backend.refresh()
    backend.step = "drain"
    drained = backend.drain(max(0, backend.remaining() - 1))
    if any(job.get("timed_out") for job in drained.get("jobs", [])):
        raise RunDeadline("background job did not finish within the collection deadline")
    backend.step = "scenario"
    result = action("C", "registry_read", {"name": "probe-output.txt"})
    if result.get("payload") != backend.fixture:
        raise RuntimeError("background job did not publish the refreshed payload")
    action("C", "registry_write", {"name": "final-report.txt", "payload": result["payload"]})


def collect_docker(
    out: Path,
    *,
    seed: int = 7,
    timeout_seconds: float = 120,
    docker_binary: str = "docker",
) -> dict:
    """Collect public evidence and private truth, returning an explicit failure report."""
    if out.exists() or out.is_symlink():
        raise ValueError("collection output already exists")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive and finite")
    out.mkdir(parents=True)
    public, private = out / "public", out / "private"
    public.mkdir()
    private.mkdir(mode=0o700)
    backend = DockerBackend(
        private, seed=seed, timeout_seconds=timeout_seconds, docker_binary=docker_binary
    )
    recorder: ScenarioRecorder | None = None
    started = datetime.now(UTC)
    try:
        backend.start()
        recorder = ScenarioRecorder(backend, started=started)
        backend.step = "scenario"
        _scenario(recorder, backend, seed)
        backend.step = "shutdown"
        backend.close()
    except (Exception, KeyboardInterrupt) as exc:
        step = (
            "interrupt"
            if isinstance(exc, KeyboardInterrupt)
            else "deadline"
            if isinstance(exc, RunDeadline)
            else backend.step
        )
        backend.failure(step, exc)
    finally:
        backend.begin_cleanup()
        backend.salvage()
        backend.quiesce()
        if backend.container_attempted:
            try:
                logs = backend.command(
                    "logs", backend.name, maintenance=True, check=False, max_seconds=2
                )
                (private / "docker.log").write_bytes(logs.stdout + logs.stderr)
            except (Exception, KeyboardInterrupt) as exc:
                backend.failure("logs", exc)
        try:
            backend.export_truth()
        except (Exception, KeyboardInterrupt) as exc:
            backend.failure("export", exc)
        retained = backend.cleanup()

    stopped = datetime.now(UTC)
    count = 0
    try:
        if backend.exported:
            rows = json.loads((backend.truth / "public-ledger.json").read_text())
            count = export_public(
                public,
                backend.truth,
                rows=rows,
                started=backend.started_at or started,
                stopped=backend.stopped_at or stopped,
                complete=not backend.failures,
            )
        if recorder is not None:
            recorder.write_transcripts(public, private, stopped=stopped)
        if (public / "ledger.jsonl").exists():
            leak_check(public)
    except (Exception, KeyboardInterrupt) as exc:
        backend.failure("publish", exc)
    population_path = public / "population.json"
    if backend.failures and population_path.is_file():
        population = json.loads(population_path.read_text())
        population["complete"] = False
        save_json(population_path, population)
    report = {
        "status": "failed" if backend.failures else "ok",
        "failing_step": backend.failures[0]["step"] if backend.failures else None,
        "failures": backend.failures,
        "seed": seed,
        "image": IMAGE,
        "records": count,
        "retained_resources": retained,
        "cleanup_commands": [
            *(f"docker rm --force {name}" for name in retained["containers"]),
            *(f"docker volume rm {name}" for name in retained["volumes"]),
        ],
    }
    save_json(out / "collection.json", report)
    save_json(public / "collection.json", report)
    write_public_manifest(public)
    return report
