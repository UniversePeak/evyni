"""Feishu private chats over its official long-connection SDK."""

import io
import json
import logging
import os
import sqlite3
from threading import Event, Thread
from uuid import NAMESPACE_URL, uuid5

from . import providers, store
from .delivery import tick

logger = logging.getLogger(__name__)


def accept(app, event, owner):
    message = event.message
    if (
        message.chat_type != "p2p"
        or message.message_type != "text"
        or event.sender.sender_id.open_id != owner
    ):
        return
    chat = "feishu:" + message.chat_id
    with store.connect(app.path, write=True) as db:
        db.execute(
            "INSERT OR IGNORE INTO inbox VALUES(?,?,?)",
            ("fs-" + message.message_id, chat, json.loads(message.content)["text"]),
        )


def drain(app, send):
    with store.connect(app.path) as db:
        events = db.execute("SELECT * FROM inbox ORDER BY rowid LIMIT 20").fetchall()
    for event in events:
        app.receive(event["chat"], event["body"], event["id"])
        with store.connect(app.path, write=True) as db:
            db.execute("DELETE FROM inbox WHERE id=?", (event["id"],))
    tick(app, "feishu", send)


def sender(client):
    from lark_oapi.api.im.v1 import (
        CreateFileRequest,
        CreateFileRequestBody,
        CreateMessageRequest,
        CreateMessageRequestBody,
    )

    def send(chat, text, voice, key, tone=""):
        kind, content = "text", {"text": text}
        if voice:
            try:
                audio = providers.speech(text, tone)
                last_page = audio.rfind(b"OggS")
                head = audio.index(b"OpusHead")
                samples = int.from_bytes(
                    audio[last_page + 6 : last_page + 14], "little"
                )
                skip = int.from_bytes(audio[head + 10 : head + 12], "little")
                body = (
                    CreateFileRequestBody.builder()
                    .file_type("opus")
                    .file_name("reply.opus")
                    .duration(max(1, (samples - skip) // 48))
                    .file(io.BytesIO(audio))
                    .build()
                )
                uploaded = client.im.v1.file.create(
                    CreateFileRequest.builder().request_body(body).build()
                )
                if uploaded.success():
                    kind, content = "audio", {"file_key": uploaded.data.file_key}
            except (RuntimeError, OSError):
                logger.info("Voice unavailable; sending text")
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat.removeprefix("feishu:"))
            .msg_type(kind)
            .content(json.dumps(content, ensure_ascii=False))
            .uuid(str(uuid5(NAMESPACE_URL, key)))
            .build()
        )
        res = client.im.v1.message.create(
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body)
            .build()
        )
        if not res.success():
            raise RuntimeError(f"Feishu returned {res.code}")

    return send


def run(app):
    try:
        import lark_oapi as lark
    except ImportError as exc:
        raise SystemExit('请先安装飞书扩展：pip install ".[feishu]"') from exc
    names = (
        "EVYNI_FEISHU_APP_ID",
        "EVYNI_FEISHU_APP_SECRET",
        "EVYNI_FEISHU_OWNER_OPEN_ID",
    )
    for name in names:
        if not os.getenv(name):
            raise SystemExit("请设置 " + name)
    app_id, secret, owner = (os.environ[n] for n in names)
    client = lark.Client.builder().app_id(app_id).app_secret(secret).build()
    send = sender(client)

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(lambda data: accept(app, data.event, owner))
        .build()
    )
    stopped = Event()

    def worker():
        while not stopped.is_set():
            try:
                drain(app, send)
            except (sqlite3.OperationalError, RuntimeError, OSError) as exc:
                logger.error("Feishu worker will retry (%s)", type(exc).__name__)
            stopped.wait(1)

    thread = Thread(target=worker, daemon=True)
    thread.start()
    try:
        lark.ws.Client(app_id, secret, event_handler=handler).start()
    finally:
        stopped.set()
