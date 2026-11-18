"""Deliver only from the active channel. A stable delivery ID survives retries."""

import logging
from datetime import datetime

from . import store

logger = logging.getLogger(__name__)


def is_quiet(hour, start, end):
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def tick(app, channel, send):
    with app.lock:
        now = app.clock()
        with store.connect(app.path) as db:
            replies = db.execute(
                "SELECT o.*,p.voice AS allowed FROM outbox o JOIN profile p USING(chat) "
                "WHERE o.state='pending' AND o.retry<=? AND o.chat LIKE ? "
                "AND NOT EXISTS (SELECT 1 FROM outbox older WHERE older.chat=o.chat "
                "AND older.state='pending' AND older.rowid<o.rowid AND older.retry>?) "
                "ORDER BY o.rowid LIMIT 20",
                (now, channel + ":%", now),
            ).fetchall()
            jobs = db.execute(
                "SELECT * FROM job WHERE state='pending' AND due<=? AND retry<=? "
                "AND chat LIKE ? ORDER BY due LIMIT 100",
                (now, now, channel + ":%"),
            ).fetchall()
        blocked = set()
        for row in replies:
            if row["chat"] not in blocked and not deliver(
                app, send, row, "outbox", row["voice"] and row["allowed"]
            ):
                blocked.add(row["chat"])
        for row in jobs:
            if row["kind"] == "checkin":
                with store.connect(app.path, write=True) as db:
                    p = db.execute(
                        "SELECT * FROM profile WHERE chat=?", (row["chat"],)
                    ).fetchone()
                    if now - row["due"] > 86400 or not p["initiative"]:
                        db.execute(
                            "UPDATE job SET state='expired' WHERE id=?", (row["id"],)
                        )
                        continue
                    if (
                        now - p["last_user"] < 600
                        or now - p["last_checkin"] < 21600
                        or is_quiet(
                            datetime.fromtimestamp(now).astimezone().hour,
                            p["quiet_start"],
                            p["quiet_end"],
                        )
                    ):
                        db.execute(
                            "UPDATE job SET retry=? WHERE id=?", (now + 60, row["id"])
                        )
                        continue
            deliver(app, send, row, "job", False)


def deliver(app, send, row, table, voice):
    try:
        send(
            row["chat"],
            row["body"],
            bool(voice),
            row["id"],
            row["tone"] if voice else "",
        )
    except (RuntimeError, OSError) as exc:
        logger.warning("Delivery pending (%s); retry in 30s", type(exc).__name__)
        with store.connect(app.path, write=True) as db:
            db.execute(
                f"UPDATE {table} SET retry=? WHERE id=?", (app.clock() + 30, row["id"])
            )
        return False
    with store.connect(app.path, write=True) as db:
        db.execute(f"UPDATE {table} SET state='sent' WHERE id=?", (row["id"],))
        if table == "job":
            db.execute(
                "INSERT INTO turn(chat,outgoing) VALUES(?,?)",
                (row["chat"], row["body"]),
            )
            if row["kind"] == "checkin":
                db.execute(
                    "UPDATE profile SET last_checkin=? WHERE chat=?",
                    (app.clock(), row["chat"]),
                )
    return True
