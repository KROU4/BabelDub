"""CLI side of the worker protocol (contract §6) and the project GPU lock.

WorkerClient starts `<env python> -u <worker.py>` inside an engine venv, sends one JSON
command per line and reads exactly one JSON answer per command. It enforces timeouts,
keeps the worker's stderr in a log file (and a short tail for error messages) and restarts
a crashed worker, replaying the last `load` command so the model is back in memory.

GpuLock is a PID file (`state/locks/gpu.lock`) that serialises GPU stages (synth, qa,
cast clustering) of one project; a lock left by a dead process is removed automatically.
"""

from __future__ import annotations

import ctypes
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, TextIO

from babeldub.core.jsonl import now_iso
from babeldub.core.output import EXIT_ENV, EXIT_ERROR, EXIT_USAGE, BabelDubError
from babeldub.core.paths import IS_WINDOWS, env_python, hf_home, workers_dir

_EOF = object()


class WorkerError(BabelDubError):
    """A worker failed to start, answered with an error to `load`, crashed or timed out (EXIT_ERROR)."""


class WorkerTimeout(WorkerError):
    pass


class WorkerCrashed(WorkerError):
    pass


def resolve_python(env: str | os.PathLike[str]) -> Path:
    """Engine env name (`qwen3tts`) -> its interpreter; a path to an interpreter is used as is."""
    text = os.fspath(env)
    is_path = isinstance(env, os.PathLike) or "/" in text or "\\" in text
    python = Path(text) if is_path else env_python(text)
    if not python.exists():
        name = Path(text).name if is_path else text
        raise BabelDubError(
            f"engine environment '{name}' is not installed ({python})",
            code=EXIT_ENV,
            hint=f"run `babeldub setup --engines {name}` (see `babeldub doctor`)",
        )
    return python


def resolve_script(script: str | os.PathLike[str]) -> Path:
    path = Path(script)
    if not path.is_absolute():
        path = workers_dir() / path
    if not path.exists():
        raise BabelDubError(f"worker script not found: {path}", code=EXIT_ERROR)
    return path


class WorkerClient:
    """JSONL request/response client for one worker process.

    >>> with WorkerClient("qwen3tts", "tts_qwen3.py", log_path=log) as w:
    ...     w.load({"op": "load", "model": path, "device": "cuda", "dtype": "bf16"})
    ...     w.request({"op": "synth", ...})
    """

    def __init__(
        self,
        env: str | os.PathLike[str],
        script: str | os.PathLike[str],
        *,
        timeout: float = 600.0,
        load_timeout: float = 1800.0,
        log_path: Path | None = None,
        extra_env: dict[str, str] | None = None,
        max_restarts: int = 3,
    ):
        self.python = resolve_python(env)
        self.script = resolve_script(script)
        self.timeout = timeout
        self.load_timeout = load_timeout
        self.log_path = log_path
        self.extra_env = dict(extra_env or {})
        self.max_restarts = max_restarts
        self.restarts = 0
        self.load_reply: dict[str, Any] | None = None
        self._load_msg: dict[str, Any] | None = None
        self._proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[Any] = queue.Queue()
        self._tail: deque[str] = deque(maxlen=40)
        self._log_fh: TextIO | None = None
        self._threads: list[threading.Thread] = []

    # ---- process management -----------------------------------------------------------
    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stderr_tail(self, n: int = 8) -> list[str]:
        return list(self._tail)[-n:]

    def _environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env.setdefault("HF_HOME", str(hf_home()))
        env.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUNBUFFERED": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "TOKENIZERS_PARALLELISM": "false",
            }
        )
        env.update(self.extra_env)
        return env

    def start(self) -> None:
        if self.alive:
            return
        if self.log_path is not None and self._log_fh is None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_fh = self.log_path.open("a", encoding="utf-8")
        self._lines = queue.Queue()
        flags = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW
        try:
            self._proc = subprocess.Popen(
                [str(self.python), "-u", "-X", "utf8", str(self.script)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=self._environment(),
                creationflags=flags,
            )
        except OSError as exc:
            raise WorkerError(f"cannot start worker {self.script.name}: {exc}") from exc
        self._log(f"--- {now_iso()} start {self.script.name} pid={self._proc.pid} python={self.python}")
        proc = self._proc
        self._threads = [
            threading.Thread(target=self._read_stdout, args=(proc, self._lines), daemon=True),
            threading.Thread(target=self._read_stderr, args=(proc,), daemon=True),
        ]
        for th in self._threads:
            th.start()

    def _read_stdout(self, proc: subprocess.Popen[str], lines: queue.Queue[Any]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)
        lines.put(_EOF)

    def _read_stderr(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            self._log(line.rstrip("\n"))

    def _log(self, line: str) -> None:
        if line.strip():
            self._tail.append(line)
        if self._log_fh is not None:
            try:
                self._log_fh.write(line + "\n")
                self._log_fh.flush()
            except (OSError, ValueError):
                pass

    def kill(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass

    def restart(self) -> None:
        if self.restarts >= self.max_restarts:
            raise WorkerCrashed(
                f"worker {self.script.name} crashed too often ({self.restarts} restarts)",
                stderr=self.stderr_tail(),
            )
        self.restarts += 1
        self.kill()
        self._log(f"--- {now_iso()} restart #{self.restarts}")
        self.start()
        if self._load_msg is not None:
            reply = self._roundtrip(self._load_msg, self.load_timeout)
            if not reply.get("ok"):
                raise WorkerError(f"worker reload failed: {reply.get('error')}", stderr=self.stderr_tail())

    def close(self, timeout: float = 15.0) -> None:
        if self._proc is not None:
            if self._proc.poll() is None:
                try:
                    assert self._proc.stdin is not None
                    self._proc.stdin.write(json.dumps({"op": "quit"}) + "\n")
                    self._proc.stdin.flush()
                    self._proc.wait(timeout=timeout)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    self.kill()
            for stream in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass
        for th in self._threads:
            th.join(timeout=2)
        if self._log_fh is not None:
            self._log_fh.close()
            self._log_fh = None

    def __enter__(self) -> WorkerClient:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- protocol -----------------------------------------------------------------------
    def _roundtrip(self, msg: dict[str, Any], timeout: float) -> dict[str, Any]:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            raise WorkerCrashed(f"worker {self.script.name} is not running", stderr=self.stderr_tail())
        try:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise WorkerCrashed(f"worker {self.script.name} closed its input: {exc}", stderr=self.stderr_tail()) from exc
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.kill()
                raise WorkerTimeout(
                    f"worker {self.script.name} did not answer '{msg.get('op')}' within {timeout:.0f}s",
                    stderr=self.stderr_tail(),
                )
            try:
                item = self._lines.get(timeout=remaining)
            except queue.Empty:
                continue
            if item is _EOF:
                code = proc.wait()
                raise WorkerCrashed(
                    f"worker {self.script.name} exited with code {code} during '{msg.get('op')}'",
                    stderr=self.stderr_tail(),
                )
            text = str(item).strip()
            if not text:
                continue
            try:
                reply = json.loads(text)
            except json.JSONDecodeError:
                self._log(f"[stdout] {text}")  # stray print from a library; not a protocol line
                continue
            if isinstance(reply, dict):
                return reply
            self._log(f"[stdout] {text}")

    def request(self, msg: dict[str, Any], timeout: float | None = None, retry: bool = True) -> dict[str, Any]:
        """Send one command and return the worker's answer (which may be {"ok": false, ...}).

        A dead worker is (re)started first. If the worker crashes while handling the command,
        it is restarted and the command is resent once (`retry`). Timeouts kill the worker
        and raise WorkerTimeout; the next request starts a fresh one.
        """
        if not self.alive:
            if self._proc is None:
                self.start()
                if self._load_msg is not None:
                    self._roundtrip(self._load_msg, self.load_timeout)
            else:
                self.restart()
        try:
            return self._roundtrip(msg, self.timeout if timeout is None else timeout)
        except WorkerCrashed:
            if not retry:
                raise
            self.restart()
            return self._roundtrip(msg, self.timeout if timeout is None else timeout)

    def load(self, msg: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        """Send the `load` command; remembered and replayed after a restart. Raises on failure."""
        msg = {**msg, "op": "load"}
        self.start()
        reply = self._roundtrip(msg, self.load_timeout if timeout is None else timeout)
        if not reply.get("ok"):
            raise WorkerError(
                f"worker {self.script.name} failed to load: {reply.get('error')}",
                hint=reply.get("hint"),
                stderr=self.stderr_tail(),
            )
        self._load_msg = msg
        self.load_reply = reply
        return reply


# ---- GPU lock ---------------------------------------------------------------------------
def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if IS_WINDOWS:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return kernel32.GetLastError() == 5  # access denied: the process exists
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class GpuLock:
    """Exclusive PID lock file. `wait_s` > 0 waits for a busy lock instead of failing."""

    def __init__(self, path: Path, owner: str = "", wait_s: float = 0.0, poll_s: float = 2.0):
        self.path = Path(path)
        self.owner = owner
        self.wait_s = wait_s
        self.poll_s = poll_s
        self.held = False

    def _holder(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _is_stale(self, info: dict[str, Any] | None) -> bool:
        if info is None:  # unreadable: stale once it is older than a minute (writer died mid-write)
            try:
                return time.time() - self.path.stat().st_mtime > 60
            except OSError:
                return True
        if info.get("host") not in (None, socket.gethostname()):
            return False  # another machine (shared folder): never steal
        return not pid_alive(int(info.get("pid", 0) or 0))

    def acquire(self) -> GpuLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + max(0.0, self.wait_s)
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                info = self._holder()
                if self._is_stale(info):
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass
                    except OSError:
                        time.sleep(0.2)
                    continue
                if time.monotonic() >= deadline:
                    holder = info or {}
                    raise BabelDubError(
                        f"GPU is busy: lock held by pid {holder.get('pid')} ({holder.get('owner') or 'unknown'})",
                        code=EXIT_USAGE,
                        hint="wait for that stage to finish or pass --wait-lock SECONDS; "
                        f"a lock of a dead process is removed automatically ({self.path})",
                        holder=holder,
                    ) from None
                time.sleep(self.poll_s)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(
                    {"pid": os.getpid(), "host": socket.gethostname(), "owner": self.owner,
                     "ts": now_iso(), "argv": sys.argv[1:4]},
                    fh,
                )
            self.held = True
            return self

    def release(self) -> None:
        if not self.held:
            return
        info = self._holder()
        if info is None or int(info.get("pid", -1)) == os.getpid():
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self.held = False

    def __enter__(self) -> GpuLock:
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()
