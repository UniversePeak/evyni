"""One consumer per home and channel. The OS releases its lock on exit."""

import os
from contextlib import contextmanager


@contextmanager
def channel_lock(home, channel):
    with (home / (channel + ".lock")).open("a+b") as handle:
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SystemExit(f"这个数据目录已有 {channel} 实例在运行。") from exc
        yield
