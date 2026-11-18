import argparse
import os
import shutil
import subprocess
from pathlib import Path
from threading import Event, Thread
from uuid import NAMESPACE_URL, uuid5

from . import __version__, providers
from .companion import PERSONA, Companion
from .delivery import tick
from .instance import channel_lock


def console(app):
    def send(chat, text, voice, key, tone):
        print("\nEvyni › " + text, flush=True)
        if voice:
            try:
                data = providers.speech(text, tone)
                folder = app.home / "audio"
                folder.mkdir(exist_ok=True)
                path = folder / (str(uuid5(NAMESPACE_URL, key)) + ".ogg")
                path.write_bytes(data)
                if player := shutil.which("ffplay"):
                    subprocess.Popen(
                        [
                            player,
                            "-nodisp",
                            "-autoexit",
                            "-loglevel",
                            "quiet",
                            str(path),
                        ],
                        stdin=subprocess.DEVNULL,
                    )
                else:
                    print("语音 › " + str(path), flush=True)
            except (providers.ProviderError, OSError):
                print("语音暂不可用，本条已用文字送达。", flush=True)

    stopped = Event()

    def timer():
        while not stopped.wait(1):
            tick(app, "local", send)

    timer_thread = Thread(target=timer, daemon=True)
    timer_thread.start()
    print("Evyni · /help 查看命令，/quit 退出。")
    try:
        tick(app, "local", send)
        while True:
            text = input("你 › ").strip()
            if text == "/quit":
                break
            if text:
                app.receive("local:me", text)
                tick(app, "local", send)
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        stopped.set()
        timer_thread.join()


def main():
    parser = argparse.ArgumentParser(prog="evyni")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--home",
        type=Path,
        default=Path(os.getenv("EVYNI_HOME", str(Path.home() / ".evyni"))),
    )
    parser.add_argument(
        "command", choices=["init", "chat", "feishu"], nargs="?", default="chat"
    )
    args = parser.parse_args()
    app = Companion(args.home)
    if args.command == "init":
        persona = app.home / "persona.md"
        if not persona.exists():
            persona.write_text(PERSONA + "\n", encoding="utf-8")
        print("角色文件 › " + str(persona))
    else:
        with channel_lock(app.home, args.command):
            if args.command == "feishu":
                from .feishu import run

                run(app)
            else:
                console(app)
