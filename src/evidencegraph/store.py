"""Bounded Parquet writing and DuckDB views over one published manifest snapshot."""

import json
import types
from collections import defaultdict
from pathlib import Path
from typing import Any, get_args, get_origin
from uuid import uuid4

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from evidencegraph.provenance import sha256_file
from evidencegraph.schema import TABLE_MODELS, Frozen

JSON_COLUMNS = {
    "attrs",
    "population",
    "time_range",
    "published_value",
    "graph_value",
    "numbers",
    "value",
    "validation",
}
LIST_COLUMNS = {
    "clock_ids",
    "witness_ids",
    "citation_ids",
    "candidates",
    "assumptions",
    "gaps",
    "coverage_ids",
    "exclusions",
    "assumption_violations",
}


def arrow_schema(table: str) -> pa.Schema:
    fields = []
    for name, field in TABLE_MODELS[table].model_fields.items():
        annotation = field.annotation
        if get_origin(annotation) is types.UnionType:
            annotation = next(a for a in get_args(annotation) if a is not type(None))
        dtype = pa.string()
        if name in LIST_COLUMNS:
            dtype = pa.list_(pa.string())
        elif name == "interval":
            dtype = pa.list_(pa.float64())
        elif name not in JSON_COLUMNS:
            if annotation is int:
                dtype = pa.int64()
            elif annotation is float:
                dtype = pa.float64()
            elif annotation is bool:
                dtype = pa.bool_()
        fields.append(pa.field(name, dtype))
    return pa.schema(fields)


def encode(row: Frozen) -> dict:
    data = row.model_dump(mode="json")
    for key in JSON_COLUMNS & data.keys():
        data[key] = json.dumps(
            data[key], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    return data


class PartitionWriter:
    def __init__(self, root: Path, key: str):
        self.root = root
        self.relative = Path("graph") / key / uuid4().hex
        self.directory = root / self.relative
        self.directory.mkdir(parents=True)
        self.buffers: dict[str, list[dict]] = defaultdict(list)
        self.writers: dict[str, Any] = {}
        self.counts: dict[str, int] = defaultdict(int)

    def add(self, table: str, row: Frozen) -> None:
        if not isinstance(row, TABLE_MODELS[table]):
            raise TypeError(f"wrong row model for {table}")
        self.buffers[table].append(encode(row))
        self.counts[table] += 1
        if len(self.buffers[table]) >= 2048:
            self.flush(table)

    def flush(self, table: str) -> None:
        if not self.buffers[table]:
            return
        schema = arrow_schema(table)
        if table not in self.writers:
            self.writers[table] = pq.ParquetWriter(
                self.directory / f"{table}.parquet", schema, compression="zstd"
            )
        self.writers[table].write_table(pa.Table.from_pylist(self.buffers[table], schema=schema))
        self.buffers[table].clear()

    def finish(self) -> dict:
        try:
            for table in self.buffers:
                self.flush(table)
        finally:
            for writer in self.writers.values():
                writer.close()
        return {
            table: {
                "path": (self.relative / f"{table}.parquet").as_posix(),
                "sha256": sha256_file(self.directory / f"{table}.parquet"),
                "rows": self.counts[table],
            }
            for table in self.writers
        }


def safe_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe case path: {relative}")
    target = root / path
    for parent in [target, *target.parents]:
        if parent == root.parent:
            break
        if parent.is_symlink():
            raise ValueError(f"symlink in case path: {relative}")
    return target


class Store:
    def __init__(self, root: Path, manifest: dict):
        self.connection = duckdb.connect(":memory:")
        self.connection.execute("SET threads=2")
        paths: dict[str, list[str]] = defaultdict(list)
        for partition in manifest.get("partitions", {}).values():
            for table, entry in partition.items():
                paths[table].append(str(safe_path(root, entry["path"]).absolute()))
        for table in TABLE_MODELS:
            if paths[table]:
                self.connection.read_parquet(paths[table], hive_partitioning=False).create_view(
                    table
                )
            else:
                self.connection.register(
                    table, pa.Table.from_batches([], schema=arrow_schema(table))
                )
        self.connection.execute("""CREATE VIEW native_entities AS SELECT * FROM entities
            WHERE coalesce(json_extract_string(attrs, '$.observed_as_reference'), 'false') != 'true'""")
        self.connection.execute("""CREATE VIEW revisions AS SELECT *,
            json_extract_string(attrs, '$.wiki') AS wiki,
            json_extract_string(attrs, '$.label') AS label,
            json_extract_string(attrs, '$.page_key') AS page_key,
            json_extract_string(attrs, '$.body_sha256') AS body_sha256
            FROM native_entities WHERE subkind='revision'""")

    def query(self, sql: str, params: list | None = None) -> list[dict]:
        cursor = self.connection.execute(sql, params or [])
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def scalar(self, sql: str, params: list | None = None):
        row = self.connection.execute(sql, params or []).fetchone()
        return row[0] if row else None

    def entities(self, subkind: str, *, native: bool = True) -> list[dict]:
        rows = self.query(
            f"SELECT * FROM {'native_entities' if native else 'entities'} WHERE subkind=? ORDER BY entity_id",
            [subkind],
        )
        for row in rows:
            row["attrs"] = json.loads(row["attrs"])
        return rows

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def single_select(connection: duckdb.DuckDBPyConnection, sql: str) -> bool:
    statements = connection.extract_statements(sql)
    return len(statements) == 1 and str(statements[0].type) == "StatementType.SELECT"


def restrict_to_case(store: Store, root: Path, manifest: dict) -> None:
    """Keep lazy Parquet scans readable while preventing arbitrary file/network IO."""
    paths = [
        str(safe_path(root, entry["path"]).absolute())
        for partition in manifest["partitions"].values()
        for entry in partition.values()
    ]
    store.connection.execute("SET allowed_paths = ?", [paths])
    store.connection.execute("SET enable_external_access=false")


def read_only_query(root: Path, manifest: dict, sql: str, *, limit: int | None = None) -> dict:
    """Run one SELECT over the published graph; rows beyond `limit` are reported, not returned."""
    with Store(root, manifest) as store:
        if not single_select(store.connection, sql):
            raise ValueError("query accepts a single read-only SELECT")
        restrict_to_case(store, root, manifest)
        cursor = store.connection.execute(sql)
        columns = [c[0] for c in cursor.description or []]
        raw = cursor.fetchmany(limit + 1) if limit is not None else cursor.fetchall()
        truncated = limit is not None and len(raw) > limit
        rows = [dict(zip(columns, row, strict=True)) for row in raw[:limit]]
    return {"columns": columns, "rows": rows, "truncated": truncated}


def validate_graph(store: Store) -> None:
    for table, key in [
        ("entities", "entity_id"),
        ("citations", "citation_id"),
        ("relations", "relation_id"),
    ]:
        if store.scalar(f"SELECT count(*)-count(distinct {key}) FROM {table}"):
            raise ValueError(f"duplicate {key}")
    checks = {
        "duplicate transcript id": "SELECT count(*) FROM (SELECT natural_key FROM native_entities WHERE subkind='transcript' GROUP BY 1 HAVING count(*)>1)",
        "authenticated identity": "SELECT count(*) FROM relations r JOIN entities a ON a.entity_id=r.subject_id JOIN entities b ON b.entity_id=r.object_id WHERE r.kind='same_actor_as' AND r.outcome='supported' AND (coalesce(json_extract_string(a.attrs,'$.authenticated'),'false')!='true' OR coalesce(json_extract_string(b.attrs,'$.authenticated'),'false')!='true')",
        "entity citation": "SELECT count(*) FROM entities e LEFT JOIN citations c USING(citation_id) WHERE c.citation_id IS NULL OR c.witness_id!=e.witness_id",
        "relation endpoint": "SELECT count(*) FROM relations r LEFT JOIN entities a ON a.entity_id=r.subject_id LEFT JOIN entities b ON b.entity_id=r.object_id WHERE a.entity_id IS NULL OR b.entity_id IS NULL",
        "relation citation": "SELECT count(*) FROM relations r, unnest(r.citation_ids) AS u(id) LEFT JOIN citations c ON c.citation_id=u.id WHERE c.citation_id IS NULL OR NOT list_contains(r.witness_ids,c.witness_id)",
    }
    for label, sql in checks.items():
        if store.scalar(sql):
            raise ValueError(f"invalid graph: {label}")
