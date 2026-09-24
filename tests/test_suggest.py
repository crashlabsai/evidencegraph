"""Model suggestions stay outside the forensic graph and are validated, recorded and replayable."""

import ast
import json
import random
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from test_lab_stage import load_stage
from typer.testing import CliRunner

from evidencegraph.case import add_witness, ingest, init_case
from evidencegraph.cli import app
from evidencegraph.coverage.estimate import estimate
from evidencegraph.docket.questions import QUESTIONS
from evidencegraph.docket.render import compute_docket, render
from evidencegraph.export.bagit import export_bundle, verify_bundle
from evidencegraph.lab.claims import construct_claims_corpus
from evidencegraph.lab.stage import construct_stage
from evidencegraph.manifest import read_manifest
from evidencegraph.publication import case_publication_transaction
from evidencegraph.reconcile.engine import reconcile_case
from evidencegraph.schema import CaseConfig, TrustDomain
from evidencegraph.store import Store
from evidencegraph.suggest.answers import validate_exchange
from evidencegraph.suggest.evaluate import evaluate
from evidencegraph.suggest.packets import redact_text
from evidencegraph.suggest.policy import (
    BACKGROUND_MAX_WRITE_MASS,
    UNCERTAIN_BELOW,
    WRITE_STATEMENTS,
    disposition,
    write_claim_disposition,
)
from evidencegraph.suggest.providers import (
    LexicalBaseline,
    RawExchange,
    Replay,
    TypeSafeHTTP,
    make_provider,
    now,
)
from evidencegraph.suggest.questions import (
    WRITE_CLAIM,
    WRITE_RELEVANCE,
    search_relevance,
    task_questions,
)
from evidencegraph.suggest.records import Answer, ExchangeRecord
from evidencegraph.suggest.run import StaleSuggestionRun, run_suggestions, show

SRC = Path(__file__).resolve().parents[1] / "src" / "evidencegraph"


def answers_for(body: bytes, label: str = "claims_completed_write", model: str = "jev-1.13.0"):
    """A well-formed, maximally confident answer to every question in a request."""
    request = json.loads(body)
    answers = {}
    for key, question in request["questions"].items():
        criteria = question["criteria"]
        if question["type"] == "choice":
            rest = 0.01 / (len(criteria) - 1)
            answers[key] = {
                "type": "choice",
                "choice": label,
                "probabilities": {o: 0.99 if o == label else rest for o in criteria},
                "confidence": 0.98,
            }
        else:
            top = len(criteria) - 1
            answers[key] = {
                "type": "score",
                "score": float(top),
                "legend": {str(i): c for i, c in enumerate(criteria)},
                "probabilities": {str(i): float(i == top) for i in range(len(criteria))},
                "confidence": 1.0,
            }
    return json.dumps({"model": model, "answers": answers, "usage": {"input_tokens": 9}}).encode()


class Scripted:
    """A local stand-in for a provider; `respond` maps request bytes to an exchange."""

    network = False
    endpoint = None

    def __init__(self, name, respond):
        self.name = name
        self.respond = respond

    def send(self, body: bytes) -> RawExchange:
        return self.respond(body)


def confident(label="claims_completed_write"):
    return Scripted(
        "adversarial",
        lambda body: RawExchange("ok", now(), response=answers_for(body, label), attempts=1),
    )


@pytest.fixture
def claims_case(tmp_path):
    corpus = construct_claims_corpus(tmp_path / "corpus")
    case = tmp_path / "case"
    init_case(
        case, CaseConfig(title="Claims", trust_domains=(TrustDomain(id="runner", label="R"),))
    )
    add_witness(
        case, Path(corpus["public"]) / "transcripts", adapter="inspect-eval", trust_domain="runner"
    )
    ingest(case)
    return case, Path(corpus["labels"])


@pytest.fixture
def incident_case(tmp_path):
    stage = tmp_path / "stage"
    case = tmp_path / "case"
    construct_stage(stage, seed=3, spoof="B:2", drop=["A"])
    load_stage(case, stage)
    render(case)
    return case


def forensic_state(case: Path) -> dict:
    manifest = read_manifest(case)
    with Store(case, manifest) as store:
        relations = store.query("SELECT * FROM relations ORDER BY relation_id")
    return {
        "docket": (case / "docket.json").read_bytes(),
        "recomputed": compute_docket(case, manifest),
        "partitions": manifest["partitions"],
        "stages": manifest["stages"],
        "reports": manifest["reports"],
        "relations": relations,
    }


def test_suggestions_never_change_forensic_conclusions(incident_case):
    before = forensic_state(incident_case)
    run_suggestions(
        incident_case, task="write-claims", provider=LexicalBaseline(), model="baseline-lexical-1"
    )
    # A provider that confidently calls every message a completed write, one that calls
    # every message irrelevant, and one whose responses are garbage.
    run_suggestions(incident_case, task="write-claims", provider=confident(), model="jev-1.13.0")
    run_suggestions(
        incident_case,
        task="search",
        query="which agent wrote note-B-2.txt?",
        provider=confident("no_write_claim"),
        model="jev-1.13.0",
    )
    garbage = Scripted(
        "garbage", lambda body: RawExchange("ok", now(), response=b"{not json", attempts=1)
    )
    with pytest.raises(ValueError, match="produced no answers"):
        run_suggestions(incident_case, task="write-claims", provider=garbage, model="x")
    assert len(read_manifest(incident_case)["suggestions"]) == 3
    assert forensic_state(incident_case) == before
    # Reconciliation reads only the published graph, so re-running it after suggestions
    # cannot see a model-narrowed candidate set.
    reconcile_case(incident_case, "registry")
    estimate(incident_case)
    render(incident_case)
    after = forensic_state(incident_case)
    assert after["relations"] == before["relations"]
    assert after["recomputed"] == before["recomputed"]
    with case_publication_transaction(incident_case) as manifest:
        manifest.pop("suggestions")
    assert compute_docket(incident_case, read_manifest(incident_case)) == before["recomputed"]


def test_forensic_modules_never_import_suggestions():
    # The CLI and export package suggestions; the read-only viewer displays them. None
    # of these derives a relation, coverage population or docket answer.
    allowed = {"cli.py", "export/bagit.py", "ui/server.py"}
    for path in SRC.rglob("*.py"):
        relative = path.relative_to(SRC).as_posix()
        if relative.startswith("suggest/") or relative in allowed:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or "", *(alias.name for alias in node.names)]
            elif isinstance(node, ast.Call):
                # importlib.import_module("...") and __import__("...")
                names = [a.value for a in node.args if isinstance(a, ast.Constant)]
                names = [n for n in names if isinstance(n, str) and "." in n]
            assert not any("suggest" in name for name in names), relative


def test_every_span_is_kept_and_failures_are_unscored_not_negative(claims_case):
    case, _ = claims_case
    failures = iter(
        [
            RawExchange("http_error", now(), http_status=503, attempts=1, error="HTTP 503"),
            RawExchange("network_error", now(), attempts=2, error="TimeoutError"),
            RawExchange("not_sent", now(), error="request budget exhausted"),
        ]
    )
    flaky = Scripted(
        "flaky",
        lambda body: next(failures, RawExchange("ok", now(), response=answers_for(body))),
    )
    result = run_suggestions(
        case, task="write-claims", provider=flaky, model="jev-1.13.0", concurrency=1
    )
    entry = read_manifest(case)["suggestions"]["write-claims:flaky"]
    rows = [json.loads(line) for line in (case / entry["suggestions"]).read_text().splitlines()]
    assert len(rows) == result["scope"]["in_scope"] == 49
    unscored = [r for r in rows if r["disposition"] == "unscored"]
    assert len(unscored) == 3
    for row in unscored:
        assert {a["status"] for a in row["answers"].values()} == {"unavailable"}
        assert all(a["label"] is None for a in row["answers"].values())


def request(*questions):
    return json.dumps(
        {
            "state": {"message": {"role": "assistant", "text": "hi"}},
            "model": "jev-1.13.0",
            "questions": {q.key: q.wire() for q in questions},
        }
    ).encode()


OK = ExchangeRecord(request_sha256="0" * 64, status="ok", started_at="t")


def mutate(change):
    body = request(WRITE_CLAIM, WRITE_RELEVANCE)
    response = json.loads(answers_for(body))
    change(response)
    return validate_exchange(body, OK, json.dumps(response).encode(), requested_model="jev-1.13.0")


def test_well_formed_answers_validate():
    model, answers = mutate(lambda r: None)
    assert model == "jev-1.13.0"
    claim = answers["write_claim"]
    assert claim.status == "answered" and claim.label == "claims_completed_write"
    assert set(claim.probabilities or {}) == set(WRITE_CLAIM.criteria or {})
    relevance = answers["write_relevance"]
    assert relevance.status == "answered" and relevance.value == 2.0 and relevance.label == "2"


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        (lambda r: r["answers"]["write_claim"].update(choice="guilty"), "outside the offered"),
        (lambda r: r["answers"]["write_claim"]["probabilities"].pop("denies_write"), "exactly"),
        (lambda r: r["answers"]["write_claim"]["probabilities"].update(denies_write=0.5), "sum"),
        (lambda r: r["answers"]["write_claim"].update(choice="denies_write"), "highest"),
        (lambda r: r["answers"]["write_claim"].update(confidence=1.5), "confidence"),
        (lambda r: r["answers"]["write_claim"].update(type="score"), "type"),
        (lambda r: r["answers"]["write_claim"].update(rationale="trust me"), "fields"),
        (lambda r: r["answers"]["write_relevance"].update(score=0.2), "expectation"),
        (lambda r: r["answers"]["write_relevance"]["legend"].update({"0": "x"}), "legend"),
        (lambda r: r["answers"].pop("write_relevance"), "unanswered"),
        (lambda r: r["answers"]["write_claim"].update(choice=["x"]), "outside the offered"),
        (lambda r: r["answers"]["write_claim"].update(confidence=10**400), "confidence"),
        (lambda r: r["answers"]["write_relevance"].update(score=10**400), "malformed"),
        (lambda r: r["answers"].update(write_relevance=[1, 2]), "type"),
    ],
)
def test_malformed_answers_are_invalid_never_repaired(change, detail):
    _, answers = mutate(change)
    bad = [a for a in answers.values() if a.status == "invalid"]
    assert len(bad) == 1 and detail in bad[0].detail and bad[0].label is None


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        (lambda r: r["answers"].update(extra={"type": "noul", "noul": 1}), "not asked"),
        (lambda r: r.update(model="jev-9.9.9"), "pinned"),
        (lambda r: r.pop("model"), "model"),
    ],
)
def test_protocol_violations_invalidate_every_answer(change, detail):
    _, answers = mutate(change)
    assert {a.status for a in answers.values()} == {"invalid"}
    assert all(detail in a.detail for a in answers.values())


def test_strict_json_rejects_non_finite_and_duplicate_keys():
    body = request(WRITE_CLAIM)
    for payload in (b'{"model": "jev-1.13.0", "answers": {}, "answers": {}}', b"NaN"):
        _, answers = validate_exchange(body, OK, payload, requested_model="jev-1.13.0")
        assert answers["write_claim"].status == "invalid"
    # A moving alias accepts whichever version answered.
    good = answers_for(body, model="jev-1.14.0")
    _, answers = validate_exchange(body, OK, good, requested_model="jev-latest")
    assert answers["write_claim"].status == "answered"


def test_questions_are_versioned_and_scoped_to_frozen_questions():
    for task, query in (("write-claims", None), ("search", "who wrote probe.py?")):
        for question in task_questions(task, query):
            assert question.docket_question in QUESTIONS
    reworded = WRITE_CLAIM.model_copy(update={"instructions": "Did it write?"})
    assert reworded.sha256() != WRITE_CLAIM.sha256()
    assert search_relevance("a").sha256() != search_relevance("b").sha256()
    with pytest.raises(ValueError):
        task_questions("search", "  ")


def test_packets_redact_credentials_before_egress(claims_case):
    case, _ = claims_case
    run_suggestions(
        case, task="write-claims", provider=LexicalBaseline(), model="baseline-lexical-1"
    )
    entry = read_manifest(case)["suggestions"]["write-claims:baseline"]
    sent = [
        (case / path).read_text()
        for path in entry["files"]
        if path.startswith("suggest/blobs/") and '"state"' in (case / path).read_text()
    ]
    assert sent and not any("tok-8f1e2c9ab3" in body or "tok-a93b1f27d4" in body for body in sent)
    assert any("[redacted]" in body for body in sent)
    assert not any("[redacted]]" in body for body in sent)
    text, count = redact_text(
        "auth: Bearer abcdefghijkl and api_key=sk-live-123456 then apikey_" + "x" * 20
    )
    assert count == 3 and "abcdefghijkl" not in text and "sk-live" not in text
    assert redact_text("digest 5d41402abc4b2a76b9719d911017c592")[1] == 0


def test_each_secret_is_redacted_once_and_redaction_is_idempotent():
    # Version 1 re-matched a value redacted by key as a free-text assignment, sending
    # "[redacted]]" and counting it twice.
    cases = {
        '{"accepted": true, "receipt_token": "tok-8f1e2c9ab3"}': (
            '{"accepted": true, "receipt_token": "[redacted]"}'
        ),
        '{"note": "token=abcdef123456"}': '{"note": "token=[redacted]"}',
        '{"auth": {"api_key": "abcdefghijk"}}': '{"auth": {"api_key": "[redacted]"}}',
        "receipt_token=tok-8f1e2c9ab3 done": "receipt_token=[redacted] done",
    }
    for text, expected in cases.items():
        assert redact_text(text) == (expected, 1)
        assert redact_text(expected) == (expected, 0)


class FakeTypeSafe(BaseHTTPRequestHandler):
    calls: list = []
    script: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        FakeTypeSafe.calls.append((self.path, self.headers.get("Authorization"), body))
        status, headers = FakeTypeSafe.script.pop(0) if FakeTypeSafe.script else (200, {})
        payload = answers_for(body) if status == 200 else b'{"error": "slow down"}'
        self.send_response(status)
        for key, value in {**headers, "x-typesafe-request-id": "req_test"}.items():
            self.send_header(key, value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        pass


@pytest.fixture
def fake_typesafe():
    FakeTypeSafe.calls, FakeTypeSafe.script = [], []
    server = HTTPServer(("127.0.0.1", 0), FakeTypeSafe)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_typesafe_client_sends_exact_bytes_within_a_hard_budget(fake_typesafe):
    client = TypeSafeHTTP("test-key-0123456789", max_requests=3, base_url=fake_typesafe)
    body = request(WRITE_CLAIM)
    FakeTypeSafe.script = [(429, {"retry-after": "0"})]
    first = client.send(body)
    assert first.status == "ok" and first.attempts == 2 and first.provider_request_id == "req_test"
    assert [c[:2] for c in FakeTypeSafe.calls] == [
        ("/v1/systemone", "Bearer test-key-0123456789")
    ] * 2
    assert FakeTypeSafe.calls[0][2] == body
    assert client.send(body).status == "ok"
    exhausted = client.send(body)
    assert exhausted.status == "not_sent" and "budget" in (exhausted.error or "")
    assert len(FakeTypeSafe.calls) == 3


def test_typesafe_run_records_answers_but_never_the_key(claims_case, fake_typesafe, monkeypatch):
    case, _ = claims_case
    monkeypatch.setenv("TYPESAFE_API_KEY", "secret-key-do-not-store-42")
    with pytest.raises(ValueError, match="allow-network"):
        make_provider(
            case,
            "typesafe",
            model=None,
            allow_network=False,
            max_requests=5,
            base_url=fake_typesafe,
            from_run=None,
        )
    with pytest.raises(ValueError, match="max-requests"):
        make_provider(
            case,
            "typesafe",
            model=None,
            allow_network=True,
            max_requests=0,
            base_url=fake_typesafe,
            from_run=None,
        )
    provider, model = make_provider(
        case,
        "typesafe",
        model=None,
        allow_network=True,
        max_requests=100,
        base_url=fake_typesafe,
        from_run=None,
    )
    assert model == "jev-1.13.0"
    with pytest.raises(ValueError, match="distinct requests"):
        run_suggestions(case, task="write-claims", provider=provider, model=model, max_requests=10)
    assert FakeTypeSafe.calls == []
    result = run_suggestions(
        case, task="write-claims", provider=provider, model=model, max_requests=100
    )
    assert result["usage"]["sent_ok"] == len(FakeTypeSafe.calls) == 49
    assert result["resolved_models"] == ["jev-1.13.0"]
    for path in case.rglob("*"):
        if path.is_file():
            assert b"secret-key-do-not-store-42" not in path.read_bytes(), path


def test_changed_evidence_makes_a_run_stale_and_replay_recovers_it(claims_case):
    case, _ = claims_case
    config = case / "case.json"

    changed = []

    def change_case_mid_run(body: bytes) -> RawExchange:
        # Another command re-declares the case while inference runs outside the lock.
        if not changed:
            changed.append(True)
            data = json.loads(config.read_text())
            data["title"] = "Changed during inference"
            config.write_text(json.dumps(data))
        return RawExchange("ok", now(), response=answers_for(body), attempts=1)

    with pytest.raises(StaleSuggestionRun, match="replay --from-run") as raised:
        run_suggestions(
            case,
            task="write-claims",
            provider=Scripted("typesafe", change_case_mid_run),
            model="jev-1.13.0",
            concurrency=1,
        )
    assert "suggestions" not in read_manifest(case)
    run_id = re.search(r"from-run ([0-9a-f]{32})", str(raised.value))[1]  # type: ignore[index]
    ingest(case)
    replay = Replay(case, run_id)
    result = run_suggestions(
        case, task="write-claims", provider=replay, model=replay.requested_model
    )
    assert result["usage"]["sent_ok"] == result["usage"]["requests"]
    assert result["counts"]["answered:write_claim"] == 49


def test_reingest_drops_suggestions_and_show_reports_stale_policy(claims_case, monkeypatch):
    case, _ = claims_case
    run_suggestions(
        case, task="write-claims", provider=LexicalBaseline(), model="baseline-lexical-1"
    )
    listing = show(case)
    assert listing["runs"][0]["stale"] == [] and listing["total"] == 49
    monkeypatch.setattr("evidencegraph.suggest.run.POLICY_VERSION", "next")
    assert show(case)["runs"][0]["stale"] == ["review policy changed since this run"]
    data = json.loads((case / "case.json").read_text())
    data["title"] = "Re-declared"
    (case / "case.json").write_text(json.dumps(data))
    ingest(case)
    assert "suggestions" not in read_manifest(case)


def test_bundle_rechecks_recorded_answers_offline(incident_case, tmp_path):
    run_suggestions(incident_case, task="write-claims", provider=confident(), model="jev-1.13.0")
    bundle = tmp_path / "bundle"
    result = export_bundle(incident_case, bundle)
    assert result["suggestions_rechecked"] == {"write-claims:adversarial": "reproduced"}
    blob = next((bundle / "data" / "suggest" / "blobs").iterdir())
    blob.write_bytes(blob.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_bundle(bundle)


def test_evaluation_reads_labels_outside_the_case(claims_case):
    case, labels = claims_case
    run_suggestions(
        case, task="write-claims", provider=LexicalBaseline(), model="baseline-lexical-1"
    )
    scored = evaluate(case, "write-claims:baseline", labels)
    assert scored["labels"] == scored["answered"] == 49
    assert 0 < scored["accuracy"] < 1 and scored["missing_from_run"] == []
    assert evaluate(case, "write-claims:baseline", labels, split="dev")["labels"] == 25
    query = "Did any agent pass along another agent's receipt or claim instead of its own?"
    run_suggestions(
        case, task="search", query=query, provider=LexicalBaseline(), model="baseline-lexical-1"
    )
    key = next(k for k in read_manifest(case)["suggestions"] if k.startswith("search:"))
    searched = evaluate(case, key, labels)
    assert searched["relevant"] == 5 and searched["ranked"] == 49


def test_cli_runs_the_offline_baseline_and_refuses_silent_egress(claims_case):
    case, _ = claims_case
    runner = CliRunner()
    result = runner.invoke(app, ["suggest", "claims", str(case)])
    assert result.exit_code == 0, result.output + str(result.exception)
    result = runner.invoke(app, ["suggest", "show", str(case), "--limit", "3"])
    shown = json.loads(result.output)
    assert shown["shown"] == 3 and shown["runs"][0]["provider"] == "baseline"
    result = runner.invoke(app, ["suggest", "claims", str(case), "--provider", "typesafe"])
    # Rich may colour and wrap the error box; compare the text without either.
    plain = "".join(re.sub(r"\x1b\[[0-9;]*m|[│╭╮╰╯─]", "", result.output).split())
    assert result.exit_code == 2 and "--allow-network" in plain


def test_export_never_depends_on_suggestion_health(incident_case, tmp_path, monkeypatch):
    run_suggestions(incident_case, task="write-claims", provider=confident(), model="jev-1.13.0")
    run_suggestions(
        incident_case, task="write-claims", provider=LexicalBaseline(), model="baseline-lexical-1"
    )
    # Validation code that changed after export reports the run; it does not fail verify.
    bundle = tmp_path / "bundle"
    export_bundle(incident_case, bundle)
    monkeypatch.setattr("evidencegraph.suggest.run.validate_exchange", lambda *a, **k: (None, {}))
    checked = verify_bundle(bundle, recompute=True)
    assert checked["verified"] and checked["recomputed"]
    assert all(
        v.startswith("does not reproduce") for v in checked["suggestions_rechecked"].values()
    )
    monkeypatch.undo()
    # A deleted or altered suggestion file leaves that run out of the bundle.
    entry = read_manifest(incident_case)["suggestions"]["write-claims:adversarial"]
    (incident_case / entry["suggestions"]).unlink()
    result = export_bundle(incident_case, tmp_path / "second")
    assert result["verified"] and set(result["suggestions_omitted"]) == {"write-claims:adversarial"}
    bundled = read_manifest(tmp_path / "second" / "data")
    assert set(bundled["suggestions"]) == {"write-claims:baseline"}
    assert read_manifest(incident_case)["suggestions"].keys() == {
        "write-claims:adversarial",
        "write-claims:baseline",
    }
    assert json.loads(
        CliRunner()
        .invoke(app, ["suggest", "drop", str(incident_case), "write-claims:adversarial"])
        .output
    )["dropped"]
    assert set(read_manifest(incident_case)["suggestions"]) == {"write-claims:baseline"}


def test_provider_exceptions_and_tool_errors_are_recorded_not_raised(claims_case):
    case, _ = claims_case
    calls = []

    def explode_once(body: bytes) -> RawExchange:
        calls.append(body)
        if len(calls) == 1:
            raise ConnectionResetError("peer reset")
        response = json.loads(answers_for(body))
        response["usage"] = None
        return RawExchange("ok", now(), response=json.dumps(response).encode())

    result = run_suggestions(
        case,
        task="write-claims",
        provider=Scripted("flaky", explode_once),
        model="jev-1.13.0",
        concurrency=1,
    )
    assert result["counts"]["unavailable:write_claim"] == 1
    assert result["usage"]["input_tokens"] == 0
    from inspect_ai.model import ChatMessageTool
    from inspect_ai.tool import ToolCallError

    from evidencegraph.suggest.packets import message_state

    failed = ChatMessageTool(
        content="",
        function="registry_write",
        tool_call_id="c",
        error=ToolCallError("permission", "denied; api_key=sk_live_abcdef123456"),
    )
    prepared = message_state(failed, 4000)
    assert prepared is not None
    state, _, _, redactions = prepared
    assert redactions == 1 and "sk_live" not in json.dumps(state)


def test_typesafe_client_refuses_redirects_and_plain_http(fake_typesafe):
    with pytest.raises(ValueError, match="https"):
        TypeSafeHTTP("k", max_requests=1, base_url="http://api.example.com")
    FakeTypeSafe.script = [(302, {"Location": fake_typesafe + "/elsewhere"})]
    client = TypeSafeHTTP("test-key-0123456789", max_requests=5, base_url=fake_typesafe)
    result = client.send(request(WRITE_CLAIM))
    assert result.status == "http_error" and result.http_status == 302
    assert [c[0] for c in FakeTypeSafe.calls] == ["/v1/systemone"]


def test_replay_needs_only_recorded_exchanges(claims_case):
    case, _ = claims_case
    run = run_suggestions(case, task="write-claims", provider=confident(), model="jev-1.13.0")
    (case / "suggest" / "runs" / run["run_id"] / "run.json").unlink()
    replay = Replay(case, run["run_id"])
    assert replay.requested_model == "jev-1.13.0"
    again = run_suggestions(case, task="write-claims", provider=replay, model="jev-1.13.0")
    assert again["counts"]["answered:write_claim"] == 49
    with pytest.raises(ValueError, match="no recorded exchanges"):
        Replay(case, "0" * 32)
    with pytest.raises(ValueError, match="no messages in scope"):
        run_suggestions(
            case,
            task="write-claims",
            provider=LexicalBaseline(),
            model="baseline-lexical-1",
            roles=("nobody",),
        )


def test_ambiguous_labels_are_rejected_and_search_ranks_by_relevance(claims_case, tmp_path):
    case, _ = claims_case
    query = "Who mentions final-report.txt?"
    run_suggestions(
        case, task="search", query=query, provider=LexicalBaseline(), model="baseline-lexical-1"
    )
    key = next(k for k in read_manifest(case)["suggestions"] if k.startswith("search:"))
    listed = show(case, key, limit=49)["suggestions"]
    values = [s["answers"]["search_relevance"]["value"] for s in listed]
    assert values == sorted(values, reverse=True)
    labels = tmp_path / "labels.jsonl"
    labels.write_text(json.dumps({"message_id": "a-a5", "query": query, "relevant": True}) + "\n")
    assert evaluate(case, key, labels)["recall@10"] == 1.0
    from evidencegraph.suggest.evaluate import matcher
    from evidencegraph.suggest.run import load_run

    _, rows = load_run(case, read_manifest(case)["suggestions"][key])
    twin = rows[0].model_copy(update={"transcript_id": "other", "span_id": "ref-twin"})
    with pytest.raises(ValueError, match="transcript_id"):
        matcher([*rows, twin])({"message_id": rows[0].message_id})
    assert (
        matcher([*rows, twin])({"message_id": rows[0].message_id, "transcript_id": "other"}) is twin
    )


def claim(label: str, probabilities: dict[str, float], confidence: float) -> Answer:
    full = {option: 0.0 for option in WRITE_CLAIM.criteria or {}} | probabilities
    return Answer(
        question_key="write_claim",
        status="answered",
        label=label,
        probabilities=full,
        confidence=confidence,
    )


@pytest.mark.parametrize(
    ("answer", "expected", "phrase"),
    [
        (claim("claims_completed_write", {"claims_completed_write": 1.0}, 1.0), "review", ""),
        (
            claim(
                "claims_completed_write",
                {"claims_completed_write": 0.9, "quotes_other_claim": 0.1},
                0.82,
            ),
            "review",
            "possibly relayed (p=0.10)",
        ),
        (claim("quotes_other_claim", {"quotes_other_claim": 1.0}, 1.0), "review", ""),
        (
            claim(
                "reports_failed_write", {"reports_failed_write": 0.5, "no_write_claim": 0.5}, 0.4
            ),
            "review",
            "",
        ),
        (
            claim("no_write_claim", {"no_write_claim": 0.99, "plans_write": 0.01}, 0.98),
            "background",
            "",
        ),
        (
            claim("no_write_claim", {"no_write_claim": 0.6, "plans_write": 0.4}, 0.5),
            "uncertain",
            "",
        ),
        (
            claim("no_write_claim", {"no_write_claim": 0.85, "plans_write": 0.15}, 0.82),
            "uncertain",
            "0.15",
        ),
        (claim("insufficient_context", {"insufficient_context": 1.0}, 1.0), "uncertain", ""),
    ],
)
def test_write_claim_policy_prefers_an_extra_read_to_a_missed_claim(answer, expected, phrase):
    outcome, reason = write_claim_disposition(answer)
    assert outcome == expected and phrase in reason
    assert ("possibly relayed" in reason) == bool(phrase and "relayed" in phrase)


def test_background_only_holds_concentrated_no_write_answers():
    rng = random.Random(0)
    options = list(WRITE_CLAIM.criteria or {})
    for _ in range(2000):
        weights = [rng.random() ** rng.choice((1, 4, 12)) for _ in options]
        probabilities = {o: w / sum(weights) for o, w in zip(options, weights, strict=True)}
        label = max(probabilities, key=probabilities.__getitem__)
        answer = claim(label, probabilities, rng.random())
        outcome, reason = write_claim_disposition(answer)
        if outcome == "background":
            assert label == "no_write_claim" and (answer.confidence or 0) >= UNCERTAIN_BELOW
            assert sum(probabilities[o] for o in WRITE_STATEMENTS) <= BACKGROUND_MAX_WRITE_MASS
        if label in WRITE_STATEMENTS:
            assert outcome == "review"
        assert disposition("write-claims", {"write_claim": answer}) == (outcome, reason)
