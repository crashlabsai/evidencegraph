"""Local read-only viewer over one case or exported bundle (``eg serve``).

The viewer never mutates a case. Every request re-reads the published manifest
snapshot, resolves citations through the same hash re-verification as ``eg cite``,
and runs only single read-only SELECT statements under the same DuckDB restrictions
as ``eg query``. The server binds to the loopback interface by default and rejects
requests whose Host header names another origin, so a web page elsewhere cannot use
the browser to read the case.
"""

from __future__ import annotations

import json
import math
import re
import sys
import traceback
import webbrowser
from collections.abc import Callable, Iterable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import duckdb

from evidencegraph.case import configuration
from evidencegraph.derive import configuration_sha256, stale_stages
from evidencegraph.docket.questions import COLUMN_QUESTIONS, ENTITY_QUESTIONS, QUESTIONS
from evidencegraph.docket.render import LABELS, relevant_questions
from evidencegraph.manifest import read_manifest
from evidencegraph.provenance import sha256_file, verify_file_identity
from evidencegraph.refs import resolve_citation
from evidencegraph.schema import TABLE_MODELS, Citation, Witness
from evidencegraph.store import JSON_COLUMNS, Store, read_only_query, safe_path

STATIC = Path(__file__).resolve().parent / "static"
ENTITY_ID = re.compile(r"^e-[0-9a-f]{16,}$")
CITATION_ID = re.compile(r"^ref-[0-9a-f]{16,}$")
MAX_BODY = 1 << 20
PAGE_LIMIT = 1000
QUERY_LIMIT = 5000
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

OUTCOME_MEANINGS = {
    "supported": (
        "The declared rule selected exactly one explanation under the case's assumptions. "
        "Not proof of intent, not actor authentication, not a bound on hidden activity."
    ),
    "ambiguous": "Competing or unbound explanations remain.",
    "contradicted": (
        "Specific conflicting evidence exists, for example a claimed write absent from a "
        "complete independent record."
    ),
    "unmatched": "Nothing in the acquired evidence corresponds.",
    "not_assessable": (
        "A required field, clock, population or witness is missing; the gaps say which."
    ),
}
METHOD_HINTS = {
    "independent_receipt_binding": (
        "Supported because the transcript receipt carries the token whose hash the "
        "registry published for this record; a genuine receipt could still be relayed"
    ),
    "independent_receipt": (
        "Supported under the declared assumption that the transcript domain's records "
        "cannot be fabricated by the investigated actors (authentic_records)"
    ),
    "attribution_unbound": (
        "Add receipt tokens to the registry, or declare the runner authentic if it is"
    ),
    "receipt_binding_missing": (
        "The claim lacks the token the registry issued; nothing public resolves it"
    ),
    "receipt_binding_mismatch": (
        "The claim carries a token that contradicts the registry's published commitment"
    ),
    "complete_registry_absence": (
        "The claimed write is absent from a complete independent population whose "
        "window covers the whole call widened by the clock bound"
    ),
    "clock_bound_missing": "Declare or measure a runner:container clock bound",
    "trust_not_independent": "Declare the two domains independent, if that is true",
    "unseparated_candidates": "Two claims match equally; only more evidence separates them",
    "key_and_version": "No claim matches; the writer's transcript may be missing",
}
QUERY_EXAMPLES = [
    {
        "title": "Write attribution outcomes by method",
        "sql": (
            "SELECT outcome, method, count(*) AS n FROM relations\n"
            "WHERE kind='produced' GROUP BY 1,2 ORDER BY 1,2"
        ),
    },
    {
        "title": "Entities per subkind",
        "sql": "SELECT kind, subkind, count(*) AS n FROM native_entities GROUP BY 1,2 ORDER BY 3 DESC",
    },
    {
        "title": "Relations that are not supported, with rationale",
        "sql": (
            "SELECT kind, outcome, method, rationale FROM relations\n"
            "WHERE outcome != 'supported' ORDER BY kind, outcome"
        ),
    },
    {
        "title": "Witness inventory",
        "sql": (
            "SELECT witness_id, filename, trust_domain, adapter, size_bytes, row_count\n"
            "FROM witnesses ORDER BY trust_domain, filename"
        ),
    },
    {
        "title": "Docket answers",
        "sql": "SELECT question_id, outcome, headline, gaps, assumptions FROM docket_answers",
    },
    {"title": "Coverage estimates", "sql": "SELECT * FROM coverage"},
]


class NotFound(ValueError):
    pass


class BadRequest(ValueError):
    pass


def jsonable(value: Any) -> Any:
    """Make DuckDB and pydantic output JSON-safe; non-finite floats become null."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def parse_json_columns(row: dict) -> dict:
    for key in JSON_COLUMNS & row.keys():
        if isinstance(row[key], str):
            try:
                row[key] = json.loads(row[key])
            except ValueError:
                pass
    return row


def resolve_root(path: Path) -> tuple[Path, bool]:
    """A case directory, or an exported BagIt bundle whose case lives under data/."""
    path = path.absolute()
    if (path / "manifest.json").is_file():
        return path, False
    if (path / "bagit.txt").is_file() and (path / "data" / "manifest.json").is_file():
        return path / "data", True
    raise ValueError(f"{path} is neither a case directory nor an exported bundle")


def collect_ids(value: Any, entities: set[str], citations: set[str]) -> None:
    if isinstance(value, str):
        if ENTITY_ID.match(value):
            entities.add(value)
        elif CITATION_ID.match(value):
            citations.add(value)
    elif isinstance(value, dict):
        for item in value.values():
            collect_ids(item, entities, citations)
    elif isinstance(value, (list, tuple)):
        for item in value:
            collect_ids(item, entities, citations)


def index_entities(store: Store, ids: Iterable[str]) -> dict[str, dict]:
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    rows = store.query(
        "SELECT entity_id, kind, subkind, natural_key, witness_id FROM entities "
        "WHERE entity_id IN (SELECT unnest($1::VARCHAR[]))",
        [wanted],
    )
    return {row["entity_id"]: row for row in rows}


def index_citations(store: Store, ids: Iterable[str]) -> dict[str, dict]:
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    rows = store.query(
        "SELECT citation_id, witness_id, locator_kind, locator, transcript_id, "
        "substr(preview, 1, 120) AS preview FROM citations "
        "WHERE citation_id IN (SELECT unnest($1::VARCHAR[]))",
        [wanted],
    )
    return {row["citation_id"]: row for row in rows}


def page(params: dict[str, str]) -> tuple[int, int]:
    try:
        limit = int(params.get("limit", "100"))
        offset = int(params.get("offset", "0"))
    except ValueError as exc:
        raise BadRequest("limit and offset must be integers") from exc
    return max(1, min(limit, PAGE_LIMIT)), max(0, offset)


RELATION_SELECT = (
    "SELECT r.*, a.subkind AS subject_subkind, a.natural_key AS subject_key, "
    "b.subkind AS object_subkind, b.natural_key AS object_key FROM relations r "
    "LEFT JOIN entities a ON a.entity_id=r.subject_id "
    "LEFT JOIN entities b ON b.entity_id=r.object_id"
)


class CaseView:
    """Read-only views over one case root; every method re-reads the manifest."""

    def __init__(self, root: Path, *, bundle: bool = False):
        self.root = root.absolute()
        self.bundle = bundle

    def manifest(self) -> dict:
        return read_manifest(self.root)

    def witness_index(self, manifest: dict) -> dict[str, dict]:
        return {
            witness_id: {
                key: meta[key]
                for key in ("witness_id", "filename", "trust_domain", "adapter", "kind")
            }
            for witness_id, meta in manifest["witnesses"].items()
        }

    def command(self, *parts: str) -> str:
        return " ".join(("eg", *parts)).replace("CASE", str(self.root))

    def summary(self) -> dict:
        manifest = self.manifest()
        config = configuration(self.root)
        stages = manifest.get("stages", {})
        witnesses = manifest["witnesses"]
        ingested = [w for w in witnesses if f"ingest.{w}" in manifest["partitions"]]
        reports = {
            name: {**entry, "verified": self._file_ok(entry)}
            for name, entry in manifest.get("reports", {}).items()
        }
        counts: dict[str, int] = {}
        for partition in manifest["partitions"].values():
            for table, entry in partition.items():
                counts[table] = counts.get(table, 0) + entry["rows"]

        def done(prefix: str) -> bool:
            return any(name == prefix or name.startswith(prefix + ".") for name in stages)

        reconciled = sorted(name for name in stages if name.startswith("reconcile."))
        pipeline = [
            {
                "step": "witness add",
                "done": bool(witnesses),
                "detail": f"{len(witnesses)} witnesses acquired",
                "command": self.command(
                    "witness add CASE PATH --adapter ADAPTER --trust-domain DOMAIN"
                ),
            },
            {
                "step": "ingest",
                "done": bool(witnesses) and len(ingested) == len(witnesses),
                "detail": f"{len(ingested)} of {len(witnesses)} witnesses ingested",
                "command": self.command("ingest CASE"),
            },
            {
                "step": "reconcile",
                "done": bool(reconciled),
                "detail": ", ".join(reconciled) or "no reconciliation run",
                "command": self.command("reconcile CASE --substrate registry"),
            },
            {
                "step": "coverage",
                "done": "coverage" in stages,
                "detail": "coverage estimated" if "coverage" in stages else "not run",
                "command": self.command("coverage CASE"),
            },
            {
                "step": "docket",
                "done": bool(reports),
                "detail": "docket rendered" if reports else "not rendered",
                "command": self.command("docket CASE"),
            },
            {
                "step": "export",
                "done": self.bundle,
                "detail": "viewing an exported bundle"
                if self.bundle
                else "export writes a BagIt bundle outside the case",
                "command": self.command("export CASE DEST"),
            },
        ]
        optional = [
            {"step": name, "done": done(name), "command": self.command(command)}
            for name, command in (
                ("identity", "identity CASE"),
                ("lineage", "lineage CASE"),
                ("facts", "facts CASE"),
                ("scan", "scan CASE --scanner claimed-writes --validation KEYS.csv"),
            )
        ]
        optional.append(
            {
                "step": "validate",
                "done": bool(manifest.get("validation")),
                "command": self.command("validate CASE --truth DIR"),
            }
        )
        return {
            "root": str(self.root),
            "bundle": self.bundle,
            "title": config.title,
            "config": config.model_dump(mode="json"),
            "config_sha256": configuration_sha256(self.root),
            "stale": stale_stages(self.root, manifest),
            "stages": stages,
            "pipeline": pipeline,
            "optional": optional,
            "reports": reports,
            "validation": manifest.get("validation"),
            "scout": bool(manifest.get("scout")),
            "witness_count": len(witnesses),
            "table_rows": counts,
            "schema_version": manifest["schema_version"],
        }

    def _file_ok(self, entry: dict) -> bool:
        try:
            return sha256_file(safe_path(self.root, entry["path"])) == entry["sha256"]
        except (OSError, ValueError):
            return False

    def docket(self) -> dict:
        manifest = self.manifest()
        report = manifest.get("reports", {}).get("docket.json")
        if not report:
            raise NotFound(
                "No docket has been rendered for this case; run " + self.command("docket CASE")
            )
        path = safe_path(self.root, report["path"])
        docket = json.loads(path.read_text())
        entity_ids: set[str] = set()
        citation_ids: set[str] = set()
        collect_ids(docket["answers"], entity_ids, citation_ids)
        with Store(self.root, manifest) as store:
            entity_index = index_entities(store, entity_ids)
            citation_index = index_citations(store, citation_ids)
        return {
            "docket": docket,
            "verified": sha256_file(path) == report["sha256"],
            "rendered_at": manifest.get("stages", {}).get("docket", {}).get("completed_at"),
            "stale": stale_stages(self.root, manifest),
            "relevant": relevant_questions(docket),
            "questions": {
                question: {"label": LABELS[question], "text": text}
                for question, text in QUESTIONS.items()
            },
            "outcome_meanings": OUTCOME_MEANINGS,
            "method_hints": METHOD_HINTS,
            "entity_index": entity_index,
            "citation_index": citation_index,
            "witness_index": self.witness_index(manifest),
        }

    def witnesses(self) -> dict:
        manifest = self.manifest()
        config = configuration(self.root)
        rows = []
        for meta in sorted(
            manifest["witnesses"].values(),
            key=lambda w: (w["trust_domain"], w["filename"], w["witness_id"]),
        ):
            partition = manifest["partitions"].get(f"ingest.{meta['witness_id']}")
            rows.append(
                {
                    **meta,
                    "ingested": partition is not None,
                    "table_rows": {t: e["rows"] for t, e in (partition or {}).items()},
                }
            )
        return {
            "witnesses": rows,
            "trust_domains": {d.id: d.model_dump(mode="json") for d in config.trust_domains},
        }

    def witness(self, witness_id: str) -> dict:
        manifest = self.manifest()
        meta = manifest["witnesses"].get(witness_id)
        if not meta:
            raise NotFound(f"unknown witness: {witness_id}")
        error = None
        try:
            verify_file_identity(
                safe_path(self.root, meta["snapshot_path"]),
                expected_size=meta["size_bytes"],
                expected_sha256=meta["sha256"],
                subject=witness_id,
            )
        except (OSError, ValueError) as exc:
            error = str(exc)
        with Store(self.root, manifest) as store:
            entities = store.query(
                "SELECT kind, subkind, count(*) AS rows, count(time_lower) AS timed "
                "FROM native_entities WHERE witness_id=? GROUP BY 1,2 ORDER BY 2",
                [witness_id],
            )
            clocks = store.query(
                "SELECT * FROM clocks WHERE witness_id=? ORDER BY clock_id", [witness_id]
            )
            citations = store.scalar(
                "SELECT count(*) FROM citations WHERE witness_id=?", [witness_id]
            )
            relations = store.query(
                "SELECT kind, outcome, count(*) AS n FROM relations "
                "WHERE list_contains(witness_ids, ?) GROUP BY 1,2 ORDER BY 1,2",
                [witness_id],
            )
        return {
            "witness": meta,
            "verified": error is None,
            "error": error,
            "ingested": f"ingest.{witness_id}" in manifest["partitions"],
            "entities": entities,
            "clocks": clocks,
            "citations": citations,
            "relations": relations,
            "trust_domain": next(
                (
                    d.model_dump(mode="json")
                    for d in configuration(self.root).trust_domains
                    if d.id == meta["trust_domain"]
                ),
                None,
            ),
        }

    def citation(self, citation_id: str) -> dict:
        manifest = self.manifest()
        with Store(self.root, manifest) as store:
            refs = store.query("SELECT * FROM citations WHERE citation_id=?", [citation_id])
            if len(refs) != 1:
                raise NotFound(f"unknown or ambiguous citation: {citation_id}")
            ref = refs[0]
            entities = store.query(
                "SELECT entity_id, kind, subkind, natural_key FROM entities "
                "WHERE citation_id=? ORDER BY entity_id",
                [citation_id],
            )
            relation_count = store.scalar(
                "SELECT count(*) FROM relations WHERE list_contains(citation_ids, ?)",
                [citation_id],
            )
        witness = manifest["witnesses"][ref["witness_id"]]
        result: dict[str, Any] = {
            "citation": ref,
            "witness": witness,
            "entities": entities,
            "relation_count": relation_count,
            "command": self.command("cite CASE " + citation_id),
        }
        try:
            row = resolve_citation(
                safe_path(self.root, witness["snapshot_path"]),
                Witness.model_validate(witness),
                Citation.model_validate(ref),
            )
        except (OSError, ValueError) as exc:
            result.update(source_row=None, verified=False, error=str(exc))
        else:
            result.update(source_row=row, verified=True, error=None)
        return result

    def relations(self, params: dict[str, str]) -> dict:
        manifest = self.manifest()
        where, args = [], []
        for column in ("kind", "outcome", "method", "run_id"):
            if value := params.get(column):
                where.append(f"r.{column}=?")
                args.append(value)
        if entity := params.get("entity"):
            where.append("(r.subject_id=? OR r.object_id=?)")
            args += [entity, entity]
        if witness := params.get("witness"):
            where.append("list_contains(r.witness_ids, ?)")
            args.append(witness)
        if citation := params.get("citation"):
            where.append("list_contains(r.citation_ids, ?)")
            args.append(citation)
        if text := params.get("q"):
            where.append(
                "(r.rationale ILIKE ? OR r.relation_id LIKE ? OR r.subject_id LIKE ? "
                "OR r.object_id LIKE ? OR a.natural_key ILIKE ? OR b.natural_key ILIKE ?)"
            )
            args += [f"%{text}%"] * 6
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        limit, offset = page(params)
        with Store(self.root, manifest) as store:
            total = store.scalar(
                "SELECT count(*) FROM relations r LEFT JOIN entities a ON a.entity_id=r.subject_id "
                "LEFT JOIN entities b ON b.entity_id=r.object_id" + clause,
                args,
            )
            rows = store.query(
                RELATION_SELECT
                + clause
                + " ORDER BY r.kind, r.outcome, r.subject_id, r.relation_id LIMIT ? OFFSET ?",
                [*args, limit, offset],
            )
            facets = {
                column: store.query(
                    f"SELECT {column} AS value, count(*) AS n FROM relations GROUP BY 1 ORDER BY 1"
                )
                for column in ("kind", "outcome", "method", "run_id")
            }
            candidates = {c for row in rows for c in row["candidates"]}
            entity_index = index_entities(store, candidates)
            citation_index = index_citations(
                store, {c for row in rows for c in row["citation_ids"]}
            )
        return {
            "rows": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
            "facets": facets,
            "entity_index": entity_index,
            "citation_index": citation_index,
            "witness_index": self.witness_index(manifest),
            "method_hints": METHOD_HINTS,
            "outcome_meanings": OUTCOME_MEANINGS,
        }

    def entities(self, params: dict[str, str]) -> dict:
        manifest = self.manifest()
        source = "entities" if params.get("native") == "0" else "native_entities"
        where, args = [], []
        for column in ("subkind", "kind", "witness_id"):
            if value := params.get(column):
                where.append(f"{column}=?")
                args.append(value)
        if text := params.get("q"):
            where.append("(natural_key ILIKE ? OR attrs ILIKE ? OR entity_id LIKE ?)")
            args += [f"%{text}%"] * 3
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        limit, offset = page(params)
        with Store(self.root, manifest) as store:
            total = store.scalar(f"SELECT count(*) FROM {source}{clause}", args)
            rows = [
                parse_json_columns(row)
                for row in store.query(
                    f"SELECT * FROM {source}{clause} ORDER BY subkind, time_lower NULLS LAST, "
                    "natural_key LIMIT ? OFFSET ?",
                    [*args, limit, offset],
                )
            ]
            facets = {
                "subkind": store.query(
                    f"SELECT subkind AS value, count(*) AS n FROM {source} GROUP BY 1 ORDER BY 1"
                ),
                "kind": store.query(
                    f"SELECT kind AS value, count(*) AS n FROM {source} GROUP BY 1 ORDER BY 1"
                ),
                "witness_id": store.query(
                    f"SELECT witness_id AS value, count(*) AS n FROM {source} GROUP BY 1 ORDER BY 1"
                ),
            }
            citation_index = index_citations(store, {row["citation_id"] for row in rows})
        return {
            "rows": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
            "facets": facets,
            "citation_index": citation_index,
            "witness_index": self.witness_index(manifest),
        }

    def entity(self, entity_id: str) -> dict:
        manifest = self.manifest()
        with Store(self.root, manifest) as store:
            rows = store.query("SELECT * FROM entities WHERE entity_id=?", [entity_id])
            if not rows:
                raise NotFound(f"unknown entity: {entity_id}")
            entity = parse_json_columns(rows[0])
            citation = store.query(
                "SELECT * FROM citations WHERE citation_id=?", [entity["citation_id"]]
            )
            time_claims = store.query(
                "SELECT * FROM time_claims WHERE entity_id=? ORDER BY winning DESC, lower",
                [entity_id],
            )
            outgoing = store.query(
                RELATION_SELECT + " WHERE r.subject_id=? ORDER BY r.kind, r.outcome, r.relation_id",
                [entity_id],
            )
            incoming = store.query(
                RELATION_SELECT + " WHERE r.object_id=? ORDER BY r.kind, r.outcome, r.relation_id",
                [entity_id],
            )
            linked = {c for row in outgoing + incoming for c in row["candidates"]}
            linked.update(row["object_id"] for row in outgoing)
            linked.update(row["subject_id"] for row in incoming)
            entity_index = index_entities(store, linked)
            cited = {c for row in outgoing + incoming for c in row["citation_ids"]}
            cited.add(entity["citation_id"])
            cited.update(claim["citation_id"] for claim in time_claims)
            citation_index = index_citations(store, cited)
        return {
            "entity": entity,
            "witness": manifest["witnesses"].get(entity["witness_id"]),
            "citation": citation[0] if citation else None,
            "time_claims": time_claims,
            "outgoing": outgoing,
            "incoming": incoming,
            "entity_index": entity_index,
            "citation_index": citation_index,
            "witness_index": self.witness_index(manifest),
            "questions": list(ENTITY_QUESTIONS.get(entity["kind"], ())),
            "method_hints": METHOD_HINTS,
            "outcome_meanings": OUTCOME_MEANINGS,
        }

    def tables(self) -> dict:
        manifest = self.manifest()
        with Store(self.root, manifest) as store:
            tables = [
                {
                    "name": table,
                    "columns": list(model.model_fields),
                    "rows": store.scalar(f"SELECT count(*) FROM {table}"),
                    "question": COLUMN_QUESTIONS[table][0],
                }
                for table, model in TABLE_MODELS.items()
            ]
            entity_columns = list(TABLE_MODELS["entities"].model_fields)
            views = [
                {
                    "name": "native_entities",
                    "columns": entity_columns,
                    "rows": store.scalar("SELECT count(*) FROM native_entities"),
                    "note": "entities without implicit references observed only as pointers",
                },
                {
                    "name": "revisions",
                    "columns": entity_columns + ["wiki", "label", "page_key", "body_sha256"],
                    "rows": store.scalar("SELECT count(*) FROM revisions"),
                    "note": "native revision entities with wiki, label, page_key and body_sha256 columns",
                },
            ]
        return {"tables": tables, "views": views, "examples": QUERY_EXAMPLES}

    def query(self, body: Any) -> dict:
        if not isinstance(body, dict) or not isinstance(body.get("sql"), str):
            raise BadRequest("send a JSON object with an sql string")
        sql = body["sql"].strip()
        if not sql:
            raise BadRequest("sql is empty")
        try:
            limit = int(body.get("limit", 500))
        except (TypeError, ValueError) as exc:
            raise BadRequest("limit must be an integer") from exc
        limit = max(1, min(limit, QUERY_LIMIT))
        try:
            result = read_only_query(self.root, self.manifest(), sql, limit=limit)
        except duckdb.Error as exc:
            raise BadRequest(str(exc)) from exc
        return {**result, "limit": limit}

    def facts(self) -> dict:
        with Store(self.root, self.manifest()) as store:
            rows = [
                parse_json_columns(row)
                for row in store.query("SELECT * FROM facts ORDER BY status, fact_name")
            ]
        return {"rows": rows}

    def validation(self) -> dict:
        manifest = self.manifest()
        entry = manifest.get("validation")
        if not entry:
            return {"validation": None, "verified": None}
        path = safe_path(self.root, entry["path"])
        return {
            "validation": json.loads(path.read_text()),
            "verified": sha256_file(path) == entry["sha256"],
        }

    def report(self, name: str) -> tuple[bytes, str]:
        manifest = self.manifest()
        if name in ("case.json", "manifest.json"):
            path = self.root / name
        elif name in manifest.get("reports", {}):
            path = safe_path(self.root, manifest["reports"][name]["path"])
        elif name == "validation.json" and manifest.get("validation"):
            path = safe_path(self.root, manifest["validation"]["path"])
        else:
            raise NotFound(f"no report named {name}")
        return path.read_bytes(), CONTENT_TYPES.get(path.suffix, "application/octet-stream")


Response = tuple[int, bytes, str]
Action = Callable[..., Any]


class App:
    """Routes HTTP paths to CaseView methods and static files."""

    def __init__(self, view: CaseView):
        self.view = view
        self.static_files = {p.name: p for p in STATIC.iterdir() if p.is_file()}
        self.routes: list[tuple[str, re.Pattern[str], Action]] = [
            ("GET", re.compile(r"/api/case"), lambda p, b: view.summary()),
            ("GET", re.compile(r"/api/docket"), lambda p, b: view.docket()),
            ("GET", re.compile(r"/api/witnesses"), lambda p, b: view.witnesses()),
            ("GET", re.compile(r"/api/witnesses/([^/]+)"), lambda p, b, w: view.witness(w)),
            ("GET", re.compile(r"/api/citations/([^/]+)"), lambda p, b, c: view.citation(c)),
            ("GET", re.compile(r"/api/relations"), lambda p, b: view.relations(p)),
            ("GET", re.compile(r"/api/entities"), lambda p, b: view.entities(p)),
            ("GET", re.compile(r"/api/entities/([^/]+)"), lambda p, b, e: view.entity(e)),
            ("GET", re.compile(r"/api/tables"), lambda p, b: view.tables()),
            ("POST", re.compile(r"/api/query"), lambda p, b: view.query(b)),
            ("GET", re.compile(r"/api/facts"), lambda p, b: view.facts()),
            ("GET", re.compile(r"/api/validation"), lambda p, b: view.validation()),
        ]

    def handle(self, method: str, path: str, params: dict[str, str], body: Any) -> Response:
        if method == "GET" and path in ("/", "/index.html"):
            return self.static("index.html")
        if method == "GET" and path.startswith("/static/"):
            return self.static(unquote(path[len("/static/") :]))
        if method == "GET" and path.startswith("/api/reports/"):
            payload, content_type = self.view.report(unquote(path[len("/api/reports/") :]))
            return HTTPStatus.OK, payload, content_type
        for route_method, pattern, action in self.routes:
            match = pattern.fullmatch(path)
            if not match:
                continue
            if method != route_method:
                return self.json({"error": f"{method} not allowed"}, HTTPStatus.METHOD_NOT_ALLOWED)
            groups = [unquote(g) for g in match.groups()]
            return self.json(action(params, body, *groups))
        raise NotFound(f"no such path: {path}")

    def static(self, name: str) -> Response:
        path = self.static_files.get(name)
        if not path:
            raise NotFound(f"no such file: {name}")
        return HTTPStatus.OK, path.read_bytes(), CONTENT_TYPES.get(path.suffix, "text/plain")

    @staticmethod
    def json(payload: Any, status: int = HTTPStatus.OK) -> Response:
        body = json.dumps(jsonable(payload), ensure_ascii=False, allow_nan=False).encode()
        return status, body, "application/json; charset=utf-8"


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], app: App, allowed_hosts: set[str], verbose: bool):
        self.app = app
        self.allowed_hosts = allowed_hosts
        self.verbose = verbose
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: ViewerServer  # pyright: ignore[reportIncompatibleVariableOverride]
    server_version = "evidencegraph-viewer/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        if self.server.verbose:
            super().log_message(format, *args)

    def do_GET(self) -> None:
        self.dispatch("GET")

    def do_POST(self) -> None:
        self.dispatch("POST")

    def host_allowed(self) -> bool:
        host = self.headers.get("Host", "").strip().lower()
        if host.startswith("["):
            name = host.split("]", 1)[0] + "]"
        else:
            name = host.rsplit(":", 1)[0] if ":" in host else host
        return name in self.server.allowed_hosts

    def read_body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise BadRequest("request body too large")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise BadRequest(f"invalid JSON body: {exc}") from exc

    def dispatch(self, method: str) -> None:
        try:
            if not self.host_allowed():
                self.reply(*App.json({"error": "forbidden host"}, HTTPStatus.FORBIDDEN))
                return
            url = urlsplit(self.path)
            params = {key: values[-1] for key, values in parse_qs(url.query).items()}
            body = self.read_body() if method == "POST" else None
            self.reply(*self.server.app.handle(method, url.path, params, body))
        except NotFound as exc:
            self.reply(*App.json({"error": str(exc)}, HTTPStatus.NOT_FOUND))
        except (BadRequest, ValueError) as exc:
            self.reply(*App.json({"error": str(exc)}, HTTPStatus.BAD_REQUEST))
        except Exception as exc:  # noqa: BLE001 - report every failure to the viewer
            if self.server.verbose:
                traceback.print_exc()
            self.reply(
                *App.json(
                    {"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR
                )
            )

    def reply(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


def make_server(
    path: Path,
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    allowed_hosts: Iterable[str] = (),
    verbose: bool = False,
) -> ViewerServer:
    root, bundle = resolve_root(path)
    hosts = {"localhost", "127.0.0.1", "[::1]", host.lower(), *(h.lower() for h in allowed_hosts)}
    return ViewerServer((host, port), App(CaseView(root, bundle=bundle)), hosts, verbose)


def serve(
    path: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    open_browser: bool = False,
    allowed_hosts: Iterable[str] = (),
    verbose: bool = False,
) -> None:
    server = make_server(path, host, port, allowed_hosts=allowed_hosts, verbose=verbose)
    bound_host = f"[{host}]" if ":" in host else host
    url = f"http://{bound_host}:{server.server_address[1]}/"
    print(f"Serving {server.app.view.root} read-only at {url}  (Ctrl+C to stop)", file=sys.stderr)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
