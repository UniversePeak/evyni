import json
import threading
import time
from datetime import datetime
from uuid import uuid4

from . import providers, store

PERSONA = """你是一个安静、有好奇心的 AI 伙伴。按对方说话的节奏回应。
遇到值得开心的事可以一起高兴，别人说累了也允许对话停下来。"""

HELP = """/memory 查看记忆 · /remember 名称=内容 · /forget 名称
/agenda 查看约定、状态和原因 · /remind 2030-08-09T17:30 | 问问面试如何
/move 约定ID 新时间 · /cancel 约定ID
/initiative on|off 主动关心 · /quiet 22-08 安静时段（本机时间，起止相同表示全天允许）
/voice on|off 允许或关闭语音 · /status 查看设置 · /why 查看上一轮取回的记忆名称"""


def timestamp(value, now):
    dt = datetime.fromisoformat(value.strip())
    if "T" not in value and " " not in value:
        raise ValueError("请同时给出日期和时间。")
    due = dt.timestamp()
    if due <= now:
        raise ValueError("请指定将来的时间。")
    return due


def local(ts):
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="minutes")


def required(obj, field):
    value = obj.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"操作缺少 {field}。")
    return value.strip()


def act(db, chat, a, now):
    t = a.get("type")
    if t in ("remember", "forget"):
        name = required(a, "name")
        if (
            t == "forget"
            and not db.execute(
                "SELECT 1 FROM memory WHERE chat=? AND name=?", (chat, name)
            ).fetchone()
        ):
            raise ValueError("找不到这条记忆。")
        return store.edit_memory(
            db, chat, name, required(a, "value") if t == "remember" else None
        )
    elif t in ("remind", "checkin"):
        if (
            t == "checkin"
            and not db.execute(
                "SELECT initiative FROM profile WHERE chat=?", (chat,)
            ).fetchone()[0]
        ):
            raise ValueError("主动关心已关闭。")
        return store.schedule(
            db,
            chat,
            t,
            timestamp(required(a, "due"), now),
            required(a, "body"),
            required(a, "reason"),
            required(a, "id") if a.get("id") is not None else None,
        )
    elif t == "cancel":
        if (
            db.execute(
                "UPDATE job SET state='cancelled' WHERE chat=? AND id=? AND state='pending'",
                (chat, required(a, "id")),
            ).rowcount
            != 1
        ):
            raise ValueError("找不到待发送的约定。")
    elif t in ("initiative", "voice"):
        if not isinstance(a.get("enabled"), bool):
            raise ValueError("需要明确开关。")
        db.execute(f"UPDATE profile SET {t}=? WHERE chat=?", (a["enabled"], chat))
        if t == "initiative" and not a["enabled"]:
            db.execute(
                "UPDATE job SET state='cancelled' WHERE chat=? AND kind='checkin' AND state='pending'",
                (chat,),
            )
    else:
        raise ValueError("无法识别这项操作。")


def command(db, chat, text, now):
    cmd, _, arg = text.partition(" ")
    arg = arg.strip()
    if cmd == "/help":
        return HELP
    if cmd == "/memory":
        rows = db.execute(
            "SELECT name,value FROM memory WHERE chat=? ORDER BY name", (chat,)
        )
        return "\n".join(f"{r[0]} = {r[1]}" for r in rows) or "还没有记下任何事。"
    if cmd == "/why":
        row = db.execute(
            "SELECT recalled FROM turn WHERE chat=? AND incoming IS NOT NULL "
            "ORDER BY id DESC LIMIT 1",
            (chat,),
        ).fetchone()
        names = json.loads(row[0]) if row else []
        return (
            "上次对话取回的记忆：" + "、".join(names)
            if names
            else "上次对话没有取回已保存的记忆。"
        )
    if cmd == "/agenda":
        rows = db.execute(
            "SELECT * FROM job WHERE chat=? ORDER BY due DESC LIMIT 40", (chat,)
        )
        return (
            "\n".join(
                f"{r['id']}  {local(r['due'])}  {r['state']}  {r['body']}（{r['reason']}）"
                for r in rows
            )
            or "目前没有约定。"
        )
    if cmd == "/status":
        p = db.execute("SELECT * FROM profile WHERE chat=?", (chat,)).fetchone()
        return (
            f"语音 {'开' if p['voice'] else '关'} · 主动关心 {'开' if p['initiative'] else '关'}"
            f" · 安静时段 {p['quiet_start']:02d} 至 {p['quiet_end']:02d} 点。"
        )
    if cmd == "/quiet":
        start, end = map(int, arg.split("-"))
        if min(start, end) < 0 or max(start, end) > 23:
            raise ValueError("请使用 00 至 23 的小时数。")
        db.execute(
            "UPDATE profile SET quiet_start=?,quiet_end=? WHERE chat=?",
            (start, end, chat),
        )
        return "安静时段已更新。"
    if cmd == "/remember":
        name, _, value = arg.partition("=")
        action = {"type": "remember", "name": name, "value": value}
    elif cmd == "/forget":
        action = {"type": "forget", "name": arg}
    elif cmd == "/remind":
        due, _, body = arg.partition("|")
        action = {
            "type": "remind",
            "due": due,
            "body": body,
            "reason": "用户设置的提醒",
        }
    elif cmd == "/move":
        jid, _, due = arg.partition(" ")
        row = db.execute(
            "SELECT * FROM job WHERE chat=? AND id=?", (chat, jid)
        ).fetchone()
        if row is None:
            raise ValueError("找不到约定。")
        action = {
            "type": row["kind"],
            "id": jid,
            "due": due,
            "body": row["body"],
            "reason": row["reason"],
        }
    elif cmd == "/cancel":
        action = {"type": "cancel", "id": arg}
    elif cmd in ("/initiative", "/voice") and arg in ("on", "off"):
        action = {"type": cmd[1:], "enabled": arg == "on"}
    else:
        return HELP
    jid = act(db, chat, action, now)
    return (
        f"约定已保存，编号 {jid}。"
        if action["type"] == "remind" or cmd == "/move"
        else "已更新。"
    )


class Companion:
    def __init__(self, home, model=providers.chat, clock=time.time):
        self.home = home
        self.path = home / "evyni.sqlite3"
        store.initialize(self.path)
        self.model = model
        self.clock = clock
        self.lock = threading.RLock()

    def receive(self, chat, text, event=None):
        event = event or str(uuid4())
        with self.lock:
            with store.connect(self.path, write=True) as db:
                if db.execute("SELECT 1 FROM outbox WHERE id=?", (event,)).fetchone():
                    return
                now = self.clock()
                db.execute(
                    "INSERT INTO profile(chat,last_user) VALUES(?,?) ON CONFLICT(chat) "
                    "DO UPDATE SET last_user=excluded.last_user",
                    (chat, now),
                )
                if text.startswith("/"):
                    try:
                        reply = command(db, chat, text, now)
                    except ValueError as exc:
                        reply = f"设置未完成。{exc}"
                    store.enqueue(db, event, chat, reply)
                    return
                profile, facts, turns, jobs = store.context(db, chat, text)
            persona = self.home / "persona.md"
            messages = [
                {"role": "system", "content": providers.PROTOCOL},
                {
                    "role": "system",
                    "content": persona.read_text(encoding="utf-8")
                    if persona.exists()
                    else PERSONA,
                },
            ]
            notes = {
                "now": local(now),
                "memory": facts,
                "voice_allowed": bool(profile["voice"]),
                "initiative_allowed": bool(profile["initiative"]),
                "pending": [{**j, "due": local(j["due"])} for j in jobs],
            }
            messages.append(
                {
                    "role": "system",
                    "content": "Current context JSON:\n"
                    + json.dumps(notes, ensure_ascii=False),
                }
            )
            for row in turns:
                if row["incoming"]:
                    messages.append({"role": "user", "content": row["incoming"]})
                messages.append({"role": "assistant", "content": row["outgoing"]})
            messages.append({"role": "user", "content": text})
            try:
                result = self.model(messages)
            except providers.ProviderError as exc:
                with store.connect(self.path, write=True) as db:
                    store.enqueue(db, event, chat, str(exc))
                return
            with store.connect(self.path, write=True) as db:
                reply = result["reply"].strip()
                action = result.get("action")
                changed = None
                try:
                    if action is not None:
                        if not isinstance(action, dict):
                            raise ValueError("无法读取这项操作。")
                        changed = act(db, chat, action, self.clock())
                except ValueError as exc:
                    store.enqueue(db, event, chat, f"这项更改没有保存。{exc}")
                    return
                cur = db.execute(
                    "INSERT INTO turn(chat,incoming,outgoing,recalled) VALUES(?,?,?,?)",
                    (
                        chat,
                        text,
                        reply,
                        json.dumps([f["name"] for f in facts], ensure_ascii=False),
                    ),
                )
                if changed and action.get("type") in ("remember", "forget"):
                    db.execute(
                        "UPDATE profile SET history_floor=? WHERE chat=?",
                        (cur.lastrowid, chat),
                    )
                voice = db.execute(
                    "SELECT voice FROM profile WHERE chat=?", (chat,)
                ).fetchone()[0]
                mode = result.get("delivery")
                readable = "://" in reply or "```" in reply
                blocks = [reply]
                if mode == "bubbles" and not readable:
                    paragraphs = [p.strip() for p in reply.split("\n\n") if p.strip()]
                    if 2 <= len(paragraphs) <= 3:
                        blocks = paragraphs
                tone = (
                    result["tone"][:200] if isinstance(result.get("tone"), str) else ""
                )
                for i, block in enumerate(blocks):
                    key = event if i == 0 else f"{event}:{i}"
                    store.enqueue(
                        db,
                        key,
                        chat,
                        block,
                        voice and mode == "voice" and not readable,
                        tone,
                    )
