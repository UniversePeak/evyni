import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from evyni import feishu, providers, store
from evyni.companion import Companion
from evyni.delivery import tick


def instant(day, hour):
    return datetime(2030, 8, day, hour).astimezone().timestamp()


class ConversationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.now = instant(8, 9)
        self.model = Mock()
        self.app = Companion(self.home, self.model, lambda: self.now)
        self.sent = []
        self.chat = "local:me"

    def deliver(self):
        tick(self.app, "local", lambda *msg: self.sent.append(msg))

    def say(self, text, action=None, reply="好的。", delivery="text", event=None):
        self.model.return_value = {
            "reply": reply,
            "action": action,
            "delivery": delivery,
        }
        self.app.receive(self.chat, text, event)
        self.deliver()
        return self.sent[-1][1]

    def test_friday_interview_moves_across_restart(self):
        self.say(
            "周五十点面试结束后问问我",
            {
                "type": "remind",
                "due": "2030-08-09T10:00",
                "body": "面试聊得怎么样？",
                "reason": "你约好面试后聊聊",
            },
        )
        self.app = Companion(self.home, self.model, lambda: self.now)
        with store.connect(self.app.path) as db:
            job = db.execute("SELECT id FROM job").fetchone()[0]
        self.say(
            "改到周六，同一时间",
            {
                "type": "remind",
                "id": job,
                "due": "2030-08-10T10:00",
                "body": "面试聊得怎么样？",
                "reason": "你把面试改到了周六",
            },
        )
        self.sent.clear()
        self.now = instant(9, 10)
        self.deliver()
        self.assertEqual(self.sent, [])
        self.now = instant(10, 10)
        self.deliver()
        self.app = Companion(self.home, self.model, lambda: self.now)
        self.deliver()
        self.assertEqual([m[1] for m in self.sent], ["面试聊得怎么样？"])
        self.assertIn("sent", self.say("/agenda"))

    def test_persona_recall_correction_forget_and_chat_isolation(self):
        (self.home / "persona.md").write_text("少用反问句。", encoding="utf-8")
        self.say("/remember 饮品=乌龙茶")
        self.say("喝什么？", reply="乌龙茶。")
        messages = self.model.call_args.args[0]
        self.assertEqual(messages[1]["content"], "少用反问句。")
        self.assertIn("饮品", self.say("/why"))
        self.say("以后改喝绿茶", {"type": "remember", "name": "饮品", "value": "绿茶"})
        self.say("明天备什么？")
        context = str(self.model.call_args.args[0])
        self.assertIn("绿茶", context)
        self.assertNotIn("乌龙茶", context)
        self.chat = "local:other"
        self.assertNotIn("绿茶", self.say("/memory"))
        self.chat = "local:me"
        self.say("/forget 饮品")
        self.say("喝什么？")
        self.assertNotIn("绿茶", str(self.model.call_args.args[0]))

    def test_voice_follows_consent_and_content(self):
        self.say("你好", delivery="voice")
        self.assertFalse(self.sent[-1][2])
        self.say("/voice on")
        self.say("我做到了", reply="太好了！", delivery="voice")
        self.assertTrue(self.sent[-1][2])
        self.say("给我链接", reply="https://example.org", delivery="voice")
        self.assertFalse(self.sent[-1][2])

    def test_bubbles_keep_order_even_after_failure(self):
        self.model.return_value = {"reply": "真的？\n\n太棒了。", "delivery": "bubbles"}
        self.app.receive(self.chat, "过啦！", "msg")
        failing = Mock(side_effect=OSError)
        tick(self.app, "local", failing)
        failing.assert_called_once()
        self.deliver()
        self.assertFalse(self.sent)
        self.now += 31
        self.deliver()
        self.assertEqual([m[1] for m in self.sent], ["真的？", "太棒了。"])

    def test_checkin_waits_for_quiet_and_current_chat(self):
        self.say("/initiative on")
        self.say(
            "今晚在做陶艺",
            {
                "type": "checkin",
                "due": "2030-08-08T23:00",
                "body": "杯子成形了吗？",
                "reason": "聊过陶艺",
            },
        )
        self.sent.clear()
        self.now = instant(8, 23)
        self.deliver()
        self.assertFalse(self.sent)
        self.now = instant(9, 9)
        self.say("稍等")
        self.assertNotIn("杯子成形了吗？", [x[1] for x in self.sent])
        self.now += 601
        self.deliver()
        self.assertEqual(self.sent[-1][1], "杯子成形了吗？")

    def test_optout_and_cancel(self):
        self.say("/initiative on")
        self.say(
            "周五有活动",
            {
                "type": "checkin",
                "due": "2030-08-09T15:00",
                "body": "活动如何？",
                "reason": "聊过活动",
            },
        )
        self.say("/initiative off")
        confirmation = self.say("/remind 2030-08-09T16:00 | 打电话")
        job_id = confirmation.split("编号 ")[1].strip("。")
        self.say("/cancel " + job_id)
        self.sent.clear()
        self.now = instant(9, 17)
        self.deliver()
        self.assertFalse(self.sent)

    def test_replayed_inbound_and_retried_outbound(self):
        self.model.return_value = {"reply": "听着呢"}
        self.app.receive(self.chat, "在吗", "incoming-1")
        self.app.receive(self.chat, "在吗", "incoming-1")
        self.model.assert_called_once()
        failing = Mock(side_effect=OSError)
        tick(self.app, "local", failing)
        self.deliver()
        self.assertFalse(self.sent)
        self.now += 31
        self.deliver()
        self.assertEqual(self.sent[0][3], failing.call_args.args[3])
        self.deliver()
        self.assertEqual(len(self.sent), 1)

    def test_failed_change_never_confirms(self):
        response = self.say(
            "改到昨天",
            {
                "type": "remind",
                "due": "2020-01-01T15:00",
                "body": "聊聊",
                "reason": "请求",
            },
            reply="改好了。",
        )
        self.assertNotIn("改好了", response)
        self.assertIn("将来", response)

    def test_feishu_private_inbox_survives_restart_and_replay(self):
        msg = SimpleNamespace(
            chat_id="room",
            chat_type="p2p",
            message_type="text",
            message_id="abc",
            content=json.dumps({"text": "/status"}),
        )
        sender = SimpleNamespace(sender_id=SimpleNamespace(open_id="owner"))
        event = SimpleNamespace(sender=sender, message=msg)
        feishu.accept(self.app, event, "someone-else")
        msg.chat_type = "group"
        feishu.accept(self.app, event, "owner")
        with store.connect(self.app.path) as db:
            self.assertFalse(db.execute("SELECT * FROM inbox").fetchall())
        msg.chat_type = "p2p"
        feishu.accept(self.app, event, "owner")
        self.app = Companion(self.home, self.model, lambda: self.now)
        send = Mock()
        feishu.drain(self.app, send)
        feishu.accept(self.app, event, "owner")
        feishu.drain(self.app, send)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], "feishu:room")

    def test_feishu_voice_uses_audio_or_original_text(self):
        try:
            import lark_oapi  # noqa: F401
        except ImportError:
            self.skipTest("Install the feishu extra to exercise SDK request builders")
        client = Mock()
        client.im.v1.file.create.return_value.data.file_key = "example_audio"
        send = feishu.sender(client)
        audio = (
            b"OggS"
            + bytes(25)
            + b"OpusHead"
            + bytes(12)
            + b"OggS"
            + bytes(2)
            + (48000).to_bytes(8, "little")
        )
        with patch("evyni.providers.speech", return_value=audio):
            send("feishu:room", "恭喜！", True, "entry")
        first = client.im.v1.message.create.call_args.args[0].request_body
        self.assertEqual(first.msg_type, "audio")
        self.assertEqual(json.loads(first.content), {"file_key": "example_audio"})
        with patch(
            "evyni.providers.speech", side_effect=providers.ProviderError("offline")
        ):
            send("feishu:room", "恭喜！", True, "entry")
        second = client.im.v1.message.create.call_args.args[0].request_body
        self.assertEqual(second.msg_type, "text")
        self.assertEqual(json.loads(second.content)["text"], "恭喜！")
        self.assertEqual(first.uuid, second.uuid)
