"""Local state. Every query that reads personal data is scoped to a conversation."""

import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
 chat TEXT PRIMARY KEY, voice INTEGER DEFAULT 0, initiative INTEGER DEFAULT 0,
 quiet_start INTEGER DEFAULT 22, quiet_end INTEGER DEFAULT 8,
 last_user REAL DEFAULT 0, last_checkin REAL DEFAULT 0, history_floor INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS memory (
 chat TEXT, name TEXT, value TEXT NOT NULL, updated REAL NOT NULL,
 PRIMARY KEY (chat, name)
);
CREATE TABLE IF NOT EXISTS turn (
 id INTEGER PRIMARY KEY, chat TEXT NOT NULL, incoming TEXT, outgoing TEXT NOT NULL,
 recalled TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS turn_chat ON turn(chat,id);
CREATE TABLE IF NOT EXISTS job (
 id TEXT PRIMARY KEY, chat TEXT NOT NULL, kind TEXT NOT NULL, due REAL NOT NULL,
 body TEXT NOT NULL, reason TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', retry REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS due_job ON job(state,due);
CREATE TABLE IF NOT EXISTS outbox (
 id TEXT PRIMARY KEY, chat TEXT NOT NULL, body TEXT NOT NULL, voice INTEGER NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', retry REAL DEFAULT 0, tone TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS pending_out ON outbox(state,retry);
CREATE TABLE IF NOT EXISTS inbox (
 id TEXT PRIMARY KEY, chat TEXT NOT NULL, body TEXT NOT NULL
);
"""


@contextmanager
def connect(path: Path, *, write=False):
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    try:
        if write:
            db.execute("BEGIN IMMEDIATE")
        with db:
            yield db
    finally:
        db.close()


def initialize(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)


def words(text):
    text = text.lower()
    terms = set(re.findall(r"[a-z0-9]+", text))
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        terms.update(run[i : i + 2] for i in range(max(1, len(run) - 1)))
    return terms


def context(db, chat, message):
    profile = db.execute("SELECT * FROM profile WHERE chat=?", (chat,)).fetchone()
    facts = [
        dict(r)
        for r in db.execute(
            "SELECT name,value FROM memory WHERE chat=? ORDER BY updated DESC", (chat,)
        )
    ]
    query = words(message)
    facts.sort(
        key=lambda f: len(query & words(f["name"] + " " + f["value"])), reverse=True
    )
    turns = db.execute(
        "SELECT incoming,outgoing FROM turn WHERE chat=? AND id>? "
        "ORDER BY id DESC LIMIT 12",
        (chat, profile["history_floor"]),
    ).fetchall()
    jobs = db.execute(
        "SELECT id,kind,due,body FROM job WHERE chat=? AND state='pending' "
        "ORDER BY due LIMIT 40",
        (chat,),
    ).fetchall()
    return dict(profile), facts[:16], list(reversed(turns)), [dict(r) for r in jobs]


def edit_memory(db, chat, name, value):
    old = db.execute(
        "SELECT value FROM memory WHERE chat=? AND name=?", (chat, name)
    ).fetchone()
    if value is None:
        db.execute("DELETE FROM memory WHERE chat=? AND name=?", (chat, name))
    else:
        db.execute(
            "INSERT INTO memory VALUES(?,?,?,?) ON CONFLICT(chat,name) DO UPDATE "
            "SET value=excluded.value,updated=excluded.updated",
            (chat, name, value, time.time()),
        )
    if old is None or old["value"] == value:
        return False
    # Old dialogue can restate a superseded fact. Start the recall window afresh.
    db.execute(
        "UPDATE profile SET history_floor=(SELECT COALESCE(MAX(id),0) FROM turn "
        "WHERE chat=?) WHERE chat=?",
        (chat, chat),
    )
    return True


def schedule(db, chat, kind, due, body, reason, job_id=None):
    if job_id:
        changed = db.execute(
            "UPDATE job SET due=?,body=?,reason=?,retry=0 WHERE id=? AND chat=? "
            "AND kind=? AND state='pending'",
            (due, body, reason, job_id, chat, kind),
        )
        if changed.rowcount != 1:
            raise ValueError("这项约定已结束或不存在。用 /agenda 查看。")
    else:
        job_id = uuid4().hex[:12]
        db.execute(
            "INSERT INTO job(id,chat,kind,due,body,reason) VALUES(?,?,?,?,?,?)",
            (job_id, chat, kind, due, body, reason),
        )
    return job_id


def enqueue(db, key, chat, body, voice=False, tone=""):
    db.execute(
        "INSERT INTO outbox(id,chat,body,voice,tone) VALUES(?,?,?,?,?)",
        (key, chat, body, int(voice), tone),
    )
