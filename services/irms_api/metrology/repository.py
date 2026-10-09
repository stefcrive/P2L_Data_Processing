from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import SOFTWARE_VERSION
from .models import Material, MethodConfig

TABLES = {"materials", "methods", "qualifications", "tests", "assets", "interventions", "raw_imports", "runs",
          "measurements", "evaluations", "qc_observations", "exclusions", "periods", "releases", "reports", "results_sessions", "session_exports", "session_sources", "qc_screenings"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def uid() -> str:
    return uuid.uuid4().hex


class Repository:
    def __init__(self, root: str | Path | None = None, *, demo=False, clock=None):
        self.root = Path(root or os.getenv("IRMS_METROLOGY_DATA_DIR", ".data/metrology")).resolve()
        if (demo or clock) and self.root == Path(".data/metrology").resolve():
            raise ValueError("Demo data requires a separate workspace")
        if clock and not demo:
            raise ValueError("A simulated clock is available only in a demo workspace")
        self.demo, self.clock = demo, clock
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / "workspace-mode.json"
        if marker.exists() and json.loads(marker.read_text(encoding="utf-8"))["demo"] != demo:
            raise ValueError("Workspace mode does not match this database")
        if not marker.exists():
            marker.write_text(encode({"demo": demo}), encoding="utf-8")
        (self.root / "blobs").mkdir(exist_ok=True)
        self.path = self.root / "metrology.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS materials(id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS methods(id TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS interventions(id TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS qualifications(id TEXT PRIMARY KEY, method_id TEXT NOT NULL REFERENCES methods(id), status TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tests(id TEXT PRIMARY KEY, qualification_id TEXT NOT NULL REFERENCES qualifications(id), data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS raw_imports(id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, raw_import_id TEXT NOT NULL REFERENCES raw_imports(id), method_id TEXT REFERENCES methods(id), qualification_id TEXT REFERENCES qualifications(id), status TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS measurements(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, qualification_id TEXT NOT NULL REFERENCES qualifications(id), data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evaluations(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), method_id TEXT NOT NULL REFERENCES methods(id), data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS qc_observations(id TEXT PRIMARY KEY, evaluation_id TEXT NOT NULL REFERENCES evaluations(id), run_id TEXT NOT NULL REFERENCES runs(id), method_id TEXT NOT NULL REFERENCES methods(id), measurement_id TEXT NOT NULL REFERENCES measurements(id), data TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(evaluation_id, measurement_id));
                CREATE TABLE IF NOT EXISTS exclusions(id TEXT PRIMARY KEY, measurement_id TEXT NOT NULL REFERENCES measurements(id), run_id TEXT NOT NULL REFERENCES runs(id), data TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(measurement_id));
                CREATE TABLE IF NOT EXISTS periods(id TEXT PRIMARY KEY, method_id TEXT NOT NULL REFERENCES methods(id), data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS releases(id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES runs(id), evaluation_id TEXT NOT NULL REFERENCES evaluations(id), method_id TEXT NOT NULL REFERENCES methods(id), data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS results_sessions(id TEXT PRIMARY KEY, method_id TEXT REFERENCES methods(id), data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS qc_screenings(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES results_sessions(id), fingerprint TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(session_id, fingerprint));
                CREATE TABLE IF NOT EXISTS session_exports(id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS session_sources(id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL, previous_hash TEXT NOT NULL, hash TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS measurements_run ON measurements(run_id);
                CREATE INDEX IF NOT EXISTS qc_method ON qc_observations(method_id);
                PRAGMA user_version=1;
            """)
            for table in ("materials", "raw_imports", "measurements", "evaluations", "qc_observations", "exclusions", "periods", "releases", "reports", "audit", "tests", "assets", "session_exports", "session_sources", "qc_screenings"):
                for operation in ("UPDATE", "DELETE"):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'Append-only scientific record'); END")
            db.execute("CREATE TRIGGER IF NOT EXISTS frozen_method BEFORE UPDATE OF data ON methods WHEN OLD.status != 'draft' BEGIN SELECT RAISE(ABORT, 'Approved method is frozen'); END")
        self.seed()
        from .reference_catalog import seed_catalog
        seed_catalog(self)
        self.migrate_results_sessions()

    def instant(self):
        return self.clock() if self.clock else datetime.now(timezone.utc)

    def timestamp(self):
        return self.instant().isoformat()

    @contextmanager
    def connect(self, write: bool = False):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def unpack(row):
        if row is None:
            raise KeyError("Record not found")
        values = dict(row)
        data = json.loads(values.pop("data"))
        return {**data, **values}

    def get(self, db, table, id_):
        assert table in TABLES
        return self.unpack(db.execute(f"SELECT * FROM {table} WHERE id=?", (id_,)).fetchone())

    def list(self, db, table, **filters):
        assert table in TABLES
        allowed = {"method_id", "run_id", "qualification_id", "measurement_id", "status", "evaluation_id", "sha256"}
        assert set(filters).issubset(allowed)
        where = " AND ".join(f"{key}=?" for key in filters)
        # Preserve insertion order when timestamps share the same clock tick.
        return [self.unpack(r) for r in db.execute(f"SELECT * FROM {table}" + (f" WHERE {where}" if where else "") + " ORDER BY rowid", tuple(filters.values()))]

    def insert(self, db, table, data, **columns):
        assert table in TABLES
        record = {"id": uid(), "data": encode(data), "created_at": self.timestamp(), **columns}
        db.execute(f"INSERT INTO {table} ({','.join(record)}) VALUES ({','.join('?' for _ in record)})", tuple(record.values()))
        return self.get(db, table, record["id"])

    def update(self, db, table, id_, data=None, status=None):
        assert table in {"methods", "runs", "qualifications", "interventions", "results_sessions"}
        self.get(db, table, id_)
        if data is not None:
            db.execute(f"UPDATE {table} SET data=? WHERE id=?", (encode(data), id_))
        if status is not None:
            db.execute(f"UPDATE {table} SET status=? WHERE id=?", (status, id_))

    def audit(self, db, action, entity_id, actor, reason, before=None, after=None):
        data = {"action": action, "entity_id": entity_id, "actor": actor, "reason": reason,
                "before": before, "after": after, "software_version": SOFTWARE_VERSION, "at": self.timestamp(), "simulation": self.demo}
        last = db.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
        previous = last[0] if last else "0" * 64
        payload = encode(data)
        digest = hashlib.sha256((previous + payload).encode()).hexdigest()
        db.execute("INSERT INTO audit(data,previous_hash,hash,created_at) VALUES(?,?,?,?)", (payload, previous, digest, data["at"]))

    def audit_log(self, db, limit=500):
        query = "SELECT * FROM audit ORDER BY id DESC" + (" LIMIT ?" if limit is not None else "")
        return [{**json.loads(r["data"]), "id": r["id"], "hash": r["hash"], "previous_hash": r["previous_hash"]} for r in db.execute(query, (limit,) if limit is not None else ())]

    def blob(self, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        path = self.root / "blobs" / digest
        try:
            with path.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("Stored source file failed its integrity check")
        return digest

    def read_blob(self, digest):
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid file digest")
        content = (self.root / "blobs" / digest).read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("Stored source file failed its integrity check")
        return content

    def seed(self):
        with self.connect(write=True) as db:
            if self.list(db, "methods"):
                return
            materials = []
            for name in ("NBS18", "NBS19", "SHP2L"):
                material = Material(name=name, aliases=[name, name.replace("NBS", "NBS ")])
                if name == "SHP2L":
                    material.assigned["d13c"].value = -.75
                    material.assigned["d13c"].uncertainty = .047
                    material.assigned["d18o"].value = -5.72
                    material.assigned["d18o"].uncertainty = .089
                    material.traceability = "User-supplied in-house Solnhofen limestone values. Uncertainty type and lot await verification."
                materials.append(self.insert(db, "materials", material.model_dump()))
            config = MethodConfig(anchor_ids=[m["id"] for m in materials[:2]], qc_id=materials[2]["id"])
            method = self.insert(db, "methods", {"config": config.model_dump(), "version": 1, "revision": 1}, status="draft")
            self.audit(db, "workspace_initialized", method["id"], "system", "Laboratory defaults supplied by the user; no method is approved", after={"method_id": method["id"]})

    def migrate_results_sessions(self):
        """Add a consultation session to earlier imports without altering their science."""
        with self.connect(write=True) as db:
            assigned = {rid for s in self.list(db, "results_sessions") for rid in s["run_ids"]}
            for index, run in enumerate(self.list(db, "runs")):
                if run["id"] in assigned:
                    continue
                method = self.get(db,"methods",run["method_id"]) if run["method_id"] else None
                qualification_id = run.get("qualification_id") or (method or {}).get("approval",{}).get("qualification_id")
                name = run["label"]
                client = ("DEMO Petrobras", "DEMO Carbonate laboratory", "DEMO Research project")[index % 3] if self.demo else "Unassigned client"
                groups = {}
                if self.demo:
                    for position, measurement in enumerate(self.list(db,"measurements",run_id=run["id"])):
                        groups[measurement["id"]] = "Qualification materials" if run["context"]=="qualification" else ("Core A" if position < run["analysis_count"] / 2 else "Core B")
                record = self.insert(db,"results_sessions",{
                    "name":name,"client":client,"project":"Metrological qualification" if run["context"]=="qualification" else "Carbonate results",
                    "context":run["context"],"qualification_id":qualification_id,"run_ids":[run["id"]],"groups":groups,"notes":"Imported before session organization was introduced.",
                    "method_name":(method or {}).get("config",{}).get("name",name),"intended_use":(method or {}).get("config",{}).get("intended_use",""),
                    "input_basis":run.get("input_basis","instrument_delta"),"preapplied_corrections":run.get("preapplied_corrections",[]),
                    "processing_evidence":run.get("processing_evidence") or "Original processing provenance retained from the imported run.",
                    "bridges":{},"simulation":self.demo},method_id=run["method_id"])
                self.audit(db,"results_session_migrated",record["id"],"system","Organize the existing import for consultation; original results and method preserved",after={"run_id":run["id"]})
