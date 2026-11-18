import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from evyni import providers


class EntryTests(unittest.TestCase):
    def test_installed_cli_and_provider_wire_protocol(self):
        calls = []
        opus = b"OggS" + bytes(25) + b"OpusHead" + bytes(12)

        class Service(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append((self.path, body))
                if self.path == "/v1/chat/completions":
                    result = {
                        "reply": "Nice work!",
                        "delivery": "voice",
                        "tone": "Warm, with a short pause.",
                    }
                    data = json.dumps(
                        {"choices": [{"message": {"content": json.dumps(result)}}]}
                    ).encode()
                elif self.path == "/v1/audio/speech":
                    data = opus
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        with (
            tempfile.TemporaryDirectory() as tmp,
            ThreadingHTTPServer(("127.0.0.1", 0), Service) as server,
        ):
            th = threading.Thread(target=server.serve_forever, daemon=True)
            th.start()
            self.addCleanup(th.join, 2)
            try:
                state = Path(tmp) / "data"
                base = f"http://127.0.0.1:{server.server_port}/v1"
                env = {
                    **os.environ,
                    "EVYNI_HOME": str(state),
                    "EVYNI_MODEL": "fixture",
                    "EVYNI_BASE_URL": base,
                    "EVYNI_TTS_BASE_URL": base,
                    "EVYNI_TTS_MODEL": "fixture",
                    "EVYNI_TTS_DIRECTIONS": "1",
                    "EVYNI_API_KEY": "",
                    "EVYNI_TTS_API_KEY": "",
                    "PYTHONIOENCODING": "utf-8",
                    "PATH": "",
                }

                def cli(*args, text=""):
                    return subprocess.run(
                        [sys.executable, "-m", "evyni", *args],
                        cwd=tmp,
                        env=env,
                        input=text,
                        capture_output=True,
                        encoding="utf-8",
                        check=True,
                        timeout=20,
                    ).stdout

                cli("init")
                (state / "persona.md").write_text("用短句。", encoding="utf-8")
                output = cli("chat", text="/voice on\n我考过了\n/quit\n")
                self.assertIn("Nice work!", output)
                self.assertIn("用短句。", str(calls[0][1]["messages"]))
                self.assertEqual(calls[1][1]["input"], "Nice work!")
                self.assertEqual(
                    calls[1][1]["instructions"], "Warm, with a short pause."
                )
                self.assertEqual(
                    next((state / "audio").glob("*.ogg")).read_bytes(), opus
                )
                self.assertIn("语音 开", cli("chat", text="/status\n/quit\n"))
            finally:
                server.shutdown()

    def test_tts_key_is_not_forwarded_across_services(self):
        fake_ogg = b"OggS" + bytes(25) + b"OpusHead"
        env = {
            "EVYNI_MODEL": "chat",
            "EVYNI_API_KEY": "test-only",
            "EVYNI_BASE_URL": "http://chat.invalid/v1",
            "EVYNI_TTS_MODEL": "tts",
            "EVYNI_TTS_BASE_URL": "http://speech.invalid/v1",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch("evyni.providers.post", return_value=fake_ogg) as post,
        ):
            providers.speech("你好", "relaxed")
        self.assertIsNone(post.call_args.args[1])
        self.assertNotIn("instructions", post.call_args.args[2])
