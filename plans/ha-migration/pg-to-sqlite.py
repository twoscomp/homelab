#!/usr/bin/env python3
"""Convert the Home Assistant recorder database from PostgreSQL to SQLite, and prove it.

Runs inside the homeassistant/home-assistant image of the SAME version as the
source database, so the target schema is exactly what that version creates.

  1. Boot a minimal HA pointed at an empty SQLite file -> HA creates the schema.
  2. Empty those tables, then copy every recorder table from Postgres, keeping
     primary keys. All Postgres reads happen in ONE repeatable-read snapshot,
     so the source may be a live database.
  3. Validate: same table set, same schema version, per-table row counts,
     min/max primary key, and a per-column fingerprint (counts, sums, text
     lengths) on both sides; entity and statistic id sets; SQLite integrity.
  4. Boot HA again on the converted file: no schema migration, no recorder
     errors, and it can record a new run.

Env: PG_URL (default: scratch rehearsal DB), WORK (default /work).
Writes WORK/rehearse.log, WORK/report.json, WORK/haconfig/home-assistant_v2.db.
"""
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path

WORK = Path(os.environ.get("WORK", "/work"))
CFG = WORK / "haconfig"
DB = CFG / "home-assistant_v2.db"
LOG = WORK / "rehearse.log"
REPORT = WORK / "report.json"
PG_URL = os.environ.get("PG_URL", "postgresql://postgres:rehearsal@pg:5432/ha")
BATCH = 20000

report = {"status": "RUNNING", "timings_s": {}, "tables": {}, "checks": [], "ranges": {}}


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def save():
    REPORT.write_text(json.dumps(report, indent=2, default=str))


def check(name, ok, detail=""):
    report["checks"].append({"check": name, "ok": bool(ok), "detail": str(detail)})
    log(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
    save()
    return ok


class timed:
    def __init__(self, key):
        self.key = key

    def __enter__(self):
        self.t0 = time.time()
        log(f"--- {self.key}")

    def __exit__(self, *exc):
        report["timings_s"][self.key] = round(time.time() - self.t0, 1)
        log(f"--- {self.key} took {report['timings_s'][self.key]}s")
        save()


def write_config():
    CFG.mkdir(parents=True, exist_ok=True)
    (CFG / "configuration.yaml").write_text(
        "homeassistant:\n"
        "  name: rehearsal\n  latitude: 0\n  longitude: 0\n  elevation: 0\n"
        "  unit_system: metric\n  time_zone: America/Chicago\n"
        "  country: US\n  currency: USD\n"
        "recorder:\n"
        f"  db_url: sqlite:///{DB}\n"
        "  auto_purge: false\n  auto_repack: false\n"
        "logger:\n  default: warning\n  logs:\n"
        "    homeassistant.bootstrap: info\n"
        "    homeassistant.components.recorder: info\n"
    )


def run_ha(label, timeout=300, settle=30):
    """Boot HA on CFG, wait until initialized, let it settle, stop it cleanly."""
    logf = CFG / "home-assistant.log"
    if logf.exists():
        logf.rename(CFG / f"home-assistant.before-{label}.log")
    out = open(WORK / f"ha-{label}.stdout", "w")
    p = subprocess.Popen(
        [sys.executable, "-m", "homeassistant", "--config", str(CFG), "--skip-pip"],
        stdout=out, stderr=subprocess.STDOUT,
    )
    t0 = time.time()
    ready = False
    while time.time() - t0 < timeout:
        if p.poll() is not None:
            break
        txt = logf.read_text(errors="replace") if logf.exists() else ""
        if "Home Assistant initialized in" in txt:
            ready = True
            break
        time.sleep(2)
    if ready:
        time.sleep(settle)
    p.send_signal(signal.SIGTERM)
    try:
        p.wait(timeout=120)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
    txt = logf.read_text(errors="replace") if logf.exists() else ""
    (WORK / f"ha-{label}.log").write_text(txt)
    return ready, txt, round(time.time() - t0, 1)


def main():
    from sqlalchemy import create_engine, event, select, text

    from homeassistant.components.recorder import db_schema

    schema_version = getattr(db_schema, "SCHEMA_VERSION", None)
    tables = {t.name: t for t in db_schema.Base.metadata.sorted_tables}
    order = list(tables)
    report["ha_schema_version"] = schema_version
    log(f"HA schema version {schema_version}; {len(order)} recorder tables: {order}")

    # ---------------------------------------------------------------- 1. schema
    if DB.exists():
        for f in CFG.glob("home-assistant_v2.db*"):
            f.unlink()
    write_config()
    with timed("boot1_create_schema"):
        ready, txt, secs = run_ha("boot1", settle=15)
    check("boot1: HA initialized and created the SQLite file", ready and DB.exists(), f"{secs}s")
    if not (ready and DB.exists()):
        raise SystemExit("schema creation failed")

    pg = create_engine(PG_URL)
    lite = create_engine(f"sqlite:///{DB}")

    @event.listens_for(lite, "connect")
    def _pragmas(dbapi_conn, _rec):
        c = dbapi_conn.cursor()
        for p in ("foreign_keys=OFF", "journal_mode=WAL", "synchronous=OFF", "cache_size=-262144"):
            c.execute(f"PRAGMA {p}")
        c.close()

    pc = pg.connect().execution_options(isolation_level="REPEATABLE READ")
    pc.execute(text("SET TRANSACTION READ ONLY"))

    pg_tables = {r[0] for r in pc.execute(text("select tablename from pg_tables where schemaname='public'"))}
    check("same table set in Postgres and HA schema", pg_tables == set(order),
          f"only_pg={sorted(pg_tables - set(order))} only_ha={sorted(set(order) - pg_tables)}")
    pg_ver = pc.execute(text("select max(schema_version) from schema_changes")).scalar()
    with lite.connect() as lc:
        lite_ver = lc.execute(text("select max(schema_version) from schema_changes")).scalar()
    check("schema version: Postgres == new SQLite == HA", pg_ver == lite_ver == schema_version,
          f"pg={pg_ver} sqlite={lite_ver} ha={schema_version}")

    # ------------------------------------------------------------------ 2. copy
    with timed("clear_new_sqlite"):
        with lite.begin() as lc:
            for name in reversed(order):
                lc.execute(tables[name].delete())

    with timed("copy_all_tables"):
        for name in order:
            t = tables[name]
            t0 = time.time()
            n = 0
            res = pc.execution_options(stream_results=True, yield_per=BATCH).execute(
                select(t).order_by(*t.primary_key.columns))
            keys = list(res.keys())
            for part in res.partitions():
                rows = [dict(zip(keys, r)) for r in part]
                with lite.begin() as lc:
                    lc.execute(t.insert(), rows)
                n += len(rows)
                if n % 500000 < BATCH:
                    log(f"    {name}: {n:,} rows")
            secs = round(time.time() - t0, 1)
            report["tables"][name] = {"rows_copied": n, "seconds": secs}
            log(f"  copied {name}: {n:,} rows in {secs}s")
            save()

    # -------------------------------------------------------------- 3. validate
    def fingerprint_sql(t, dialect):
        parts = ["count(*)"]
        labels = ["rows"]
        for pk in t.primary_key.columns:
            parts += [f'min("{pk.name}")', f'max("{pk.name}")']
            labels += [f"min({pk.name})", f"max({pk.name})"]
        for col in t.columns:
            q = f'"{col.name}"'
            try:
                pt = col.type.python_type
            except Exception:
                pt = None
            parts.append(f"count({q})")
            labels.append(f"count({col.name})")
            if pt is bool:
                parts.append(f"sum(case when {q} then 1 else 0 end)")
                labels.append(f"true({col.name})")
            elif pt in (int, float):
                parts.append(f"sum({q})::float8" if dialect == "pg" else f"total({q})")
                labels.append(f"sum({col.name})")
            elif pt is str:
                parts.append(f"sum(length({q}))::float8" if dialect == "pg" else f"total(length({q}))")
                labels.append(f"len({col.name})")
        return f'SELECT {", ".join(parts)} FROM "{t.name}"', labels

    def same(a, b):
        a = 0 if a is None else a
        b = 0 if b is None else b
        if isinstance(a, float) or isinstance(b, float):
            return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-6)
        return a == b

    with timed("validate"):
        with lite.connect() as lc:
            for name in order:
                t = tables[name]
                sql_pg, labels = fingerprint_sql(t, "pg")
                sql_lite, _ = fingerprint_sql(t, "lite")
                a = pc.execute(text(sql_pg)).one()
                b = lc.execute(text(sql_lite)).one()
                diffs = [f"{lab}: pg={x} sqlite={y}" for lab, x, y in zip(labels, a, b) if not same(x, y)]
                report["tables"][name]["pg_rows"] = a[0]
                report["tables"][name]["sqlite_rows"] = b[0]
                check(f"{name}: rows, key range and {len(labels) - 3} column fingerprints match",
                      not diffs, f"{a[0]:,} rows" if not diffs else "; ".join(diffs[:6]))

            for name, col in (("states_meta", "entity_id"), ("statistics_meta", "statistic_id")):
                pa = {r[0] for r in pc.execute(text(f'select "{col}" from "{name}"'))}
                pb = {r[0] for r in lc.execute(text(f'select "{col}" from "{name}"'))}
                check(f"{name}: identical set of {col}s", pa == pb, f"{len(pa)} ids")

            for name, col in (("statistics", "start_ts"), ("statistics_short_term", "start_ts"),
                              ("states", "last_updated_ts")):
                ra = pc.execute(text(f'select min("{col}"), max("{col}") from "{name}"')).one()
                rb = lc.execute(text(f'select min("{col}"), max("{col}") from "{name}"')).one()
                fmt = lambda v: time.strftime("%Y-%m-%d %H:%M", time.localtime(v)) if v else None
                report["ranges"][name] = {"from": fmt(ra[0]), "to": fmt(ra[1])}
                check(f"{name}: same time range", same(ra[0], rb[0]) and same(ra[1], rb[1]),
                      f"{fmt(rb[0])} -> {fmt(rb[1])}")

    pc.close()

    with timed("sqlite_integrity"):
        with lite.connect() as lc:
            lc.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            ic = [r[0] for r in lc.execute(text("PRAGMA integrity_check"))]
            fk = list(lc.execute(text("PRAGMA foreign_key_check")))
    check("SQLite integrity_check", ic == ["ok"], ic[:3])
    check("SQLite foreign_key_check", not fk, f"{len(fk)} violations")
    lite.dispose()
    report["sqlite_bytes"] = DB.stat().st_size
    log(f"SQLite file: {DB.stat().st_size / 1e9:.2f} GB")

    # ------------------------------------------------------------ 4. boot test
    def counts():
        e = create_engine(f"sqlite:///{DB}")
        with e.connect() as c:
            r = {
                "schema_rows": c.execute(text("select count(*) from schema_changes")).scalar(),
                "schema_max": c.execute(text("select max(schema_version) from schema_changes")).scalar(),
                "recorder_runs": c.execute(text("select count(*) from recorder_runs")).scalar(),
                "statistics": c.execute(text("select count(*) from statistics")).scalar(),
            }
        e.dispose()
        return r

    before = counts()
    with timed("boot2_on_converted_db"):
        ready, txt, secs = run_ha("boot2", settle=45)
    after = counts()
    report["boot2"] = {"before": before, "after": after}
    errors = [l for l in txt.splitlines()
              if " ERROR " in l and re.search(r"recorder|sqlalchemy|database|sqlite", l, re.I)]
    upgrade = [l for l in txt.splitlines() if re.search(r"upgrade.*schema|schema.*upgrade|Database is about to", l, re.I)]
    check("boot2: HA initialized on the converted database", ready, f"{secs}s")
    check("boot2: no recorder / database errors in the log", not errors, errors[:3])
    check("boot2: no schema migration triggered", not upgrade and after["schema_rows"] == before["schema_rows"]
          and after["schema_max"] == schema_version, upgrade[:2] or f"schema {after['schema_max']}")
    check("boot2: recorder wrote a new run (database is writable)",
          after["recorder_runs"] == before["recorder_runs"] + 1,
          f"recorder_runs {before['recorder_runs']} -> {after['recorder_runs']}")
    check("boot2: long-term statistics intact", after["statistics"] >= before["statistics"],
          f"{before['statistics']:,} -> {after['statistics']:,}")


if __name__ == "__main__":
    WORK.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    try:
        main()
        failed = [c for c in report["checks"] if not c["ok"]]
        report["status"] = "PASS" if not failed else f"FAIL ({len(failed)} checks)"
    except BaseException as exc:
        report["status"] = f"ERROR: {exc!r}"
        report["traceback"] = traceback.format_exc()
        log(traceback.format_exc())
    report["timings_s"]["total_job"] = round(time.time() - t_start, 1)
    save()
    log(f"STATUS {report['status']}")
    os.system(f"chown -R 1000:1000 {WORK}")
    sys.exit(0 if report["status"] == "PASS" else 1)
