from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024
GENERATIONS = 5


class RotatingLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, chunk: bytes) -> None:
        if not chunk:
            return
        with self.lock:
            while chunk:
                self._reject_symlink(self.path)
                current_size = self.path.stat().st_size if self.path.exists() else 0
                if current_size >= MAX_BYTES:
                    self._rotate()
                    current_size = 0
                size = min(MAX_BYTES - current_size, len(chunk))
                with self.path.open("ab") as target:
                    target.write(chunk[:size])
                chunk = chunk[size:]

    @staticmethod
    def _reject_symlink(path: Path) -> None:
        if path.is_symlink():
            raise OSError(f"refusing symlinked log path: {path}")

    def _rotate(self) -> None:
        oldest = self.path.with_name(f"{self.path.name}.{GENERATIONS}")
        self._reject_symlink(oldest)
        oldest.unlink(missing_ok=True)
        for generation in range(GENERATIONS - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{generation}")
            target = self.path.with_name(f"{self.path.name}.{generation + 1}")
            self._reject_symlink(source)
            self._reject_symlink(target)
            if source.exists():
                os.replace(source, target)
        self._reject_symlink(self.path)
        if self.path.exists():
            os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))


def _stream(source, target: RotatingLog) -> None:
    try:
        while chunk := source.read(64 * 1024):
            target.write(chunk)
    finally:
        source.close()


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        os.killpg(process.pid, signal.SIGTERM)
    else:
        process.terminate()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a child process with bounded stdout/stderr logs")
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command or args.command[0] != "--" or len(args.command) == 1:
        parser.error("command must follow --")

    runtime = Path(args.runtime).resolve()
    cwd = Path(args.cwd).resolve(strict=True)
    runtime.mkdir(parents=True, exist_ok=True)
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        args.command[1:],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name == "posix",
        creationflags=creationflags,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    stdout = RotatingLog(runtime / f"{args.name}.out.log")
    stderr = RotatingLog(runtime / f"{args.name}.err.log")
    readers = [
        threading.Thread(target=_stream, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=_stream, args=(process.stderr, stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()

    def stop(_signum, _frame) -> None:
        _terminate(process)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    status = process.wait()
    for reader in readers:
        reader.join(timeout=2)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
