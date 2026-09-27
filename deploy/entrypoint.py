"""Container entrypoint: make the state volume writable, then drop root.

Railway mounts the volume at /app/data owned by root, while the app runs as the
unprivileged `stobox` user (uid 10001). Without this step every state write
fails with `Permission denied` (27.09.2026: 271 failures in a week; XP, message
log and the one-shot broadcast dedupe were not persisting). The entrypoint runs
as root only long enough to hand the volume to `stobox`, then execs the app
with root dropped for good.
"""

from __future__ import annotations

import os
import sys

UID = GID = 10001


def _chown_tree(path: str) -> None:
    for root, dirs, files in os.walk(path):
        for name in [root, *(os.path.join(root, d) for d in dirs),
                     *(os.path.join(root, f) for f in files)]:
            try:
                os.lchown(name, UID, GID)
            except OSError as exc:
                print(f"entrypoint: chown failed for {name}: {exc}", file=sys.stderr)


def main() -> None:
    data = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or "/app/data"
    if os.getuid() == 0:
        os.makedirs(data, exist_ok=True)
        _chown_tree(data)
        os.setgroups([])
        os.setgid(GID)
        os.setuid(UID)
        os.environ["HOME"] = "/home/stobox"
    argv = sys.argv[1:] or ["python", "-m", "stobox_ai"]
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main()
