"""
Structured logging system for the Storyboard AI pipeline.

Provides:
- ContextLogger: hierarchical logger with run_id / scene_id / step_tag binding
- setup_logging(): one-call initialisation for console + file + DB handlers
- @log_step: decorator that logs entry/exit/timing for any function
- @log_subprocess: decorator that captures subprocess stdout/stderr
- FFmpegLogCapture: context manager for FFmpeg stderr progress parsing

Usage::

    from log_utils import setup_logging, ContextLogger

    run_id = "run_20260712_143000"
    logger = setup_logging(run_id=run_id, output_dir="/path/to/output")

    logger.info("Pipeline started", extra={"language": "english"})
    scene_logger = logger.bind(scene_id=1, step_tag="image_gen")
    scene_logger.info("Generating image...")
"""

import collections
import datetime
import functools
import json
import logging
import logging.handlers
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

# ---------------------------------------------------------------------------
# ANSI colour codes (Windows 10+ supports ANSI in conhost / Windows Terminal)
# ---------------------------------------------------------------------------
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

COLORS: Dict[str, str] = {
    "DEBUG": "\033[36m",       # cyan
    "INFO": "\033[32m",        # green
    "WARNING": "\033[33m",     # yellow
    "ERROR": "\033[31m",       # red
    "CRITICAL": "\033[1;31m",  # bold red
}

LEVEL_ICONS: Dict[str, str] = {
    "DEBUG": "🔍",
    "INFO": "✓",
    "WARNING": "⚠",
    "ERROR": "✗",
    "CRITICAL": "💥",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_serializable(obj: Any) -> Any:
    """Recursively convert an object to a JSON-serialisable form."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_ensure_serializable(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): _ensure_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, bytes):
        return f"<bytes len={len(obj)}>"
    # Fallback: string representation, truncated
    s = str(obj)
    return s[:500] if len(s) > 500 else s


def _truncate(s: str, max_len: int = 200) -> str:
    """Truncate a string for console display."""
    if len(s) <= max_len:
        return s
    return s[:max_len - 3] + "..."


# ---------------------------------------------------------------------------
# JSON formatter (file output — one line per record, machine-readable)
# ---------------------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        obj: Dict[str, Any] = {
            "ts": datetime.datetime.fromtimestamp(
                record.created, tz=datetime.timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Context fields
        for key in ("run_id", "scene_id", "step_tag"):
            val = getattr(record, key, None)
            if val is not None:
                obj[key] = val

        # Extra payload
        extra = getattr(record, "log_extra", None)
        if extra:
            obj["extra"] = _ensure_serializable(extra)

        # Exception info
        if record.exc_info and record.exc_info[0]:
            obj["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info),
            }

        # Source location
        obj["loc"] = f"{record.pathname}:{record.lineno}:{record.funcName}"

        return json.dumps(obj, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Coloured console formatter (terminal output — human-readable)
# ---------------------------------------------------------------------------


class ColoredConsoleFormatter(logging.Formatter):
    """Human-readable coloured output with icons and structured info."""

    def format(self, record: logging.LogRecord) -> str:
        level = record.levelname
        color = COLORS.get(level, "")
        icon = LEVEL_ICONS.get(level, "·")

        # Build context prefix: [run_id:scene_id:step]
        context_parts = []
        run_id = getattr(record, "run_id", None)
        scene_id = getattr(record, "scene_id", None)
        step_tag = getattr(record, "step_tag", None)

        if run_id:
            context_parts.append(f"{DIM}{run_id[:8]}{RESET}")
        if scene_id is not None:
            context_parts.append(f"Scene{scene_id}")
        if step_tag:
            context_parts.append(step_tag)

        context_str = f"{DIM}[{':'.join(context_parts)}]{RESET} " if context_parts else ""

        # Timestamp
        ts = datetime.datetime.fromtimestamp(record.created).strftime("%H:%M:%S")

        # Message
        msg = record.getMessage()

        # Extra payload
        extra = getattr(record, "log_extra", None)
        extra_str = ""
        if extra and isinstance(extra, dict):
            # Show a few key fields inline
            compact = {}
            for k, v in extra.items():
                if k in ("traceback", "exception"):
                    continue
                compact[k] = _ensure_serializable(v)
            if compact:
                extra_str = f" {DIM}{_truncate(json.dumps(compact, ensure_ascii=False, default=str), 120)}{RESET}"

        # Base line
        line = f"{DIM}{ts}{RESET} {color}{BOLD}{icon}{RESET} {context_str}{msg}{extra_str}"

        # Exception traceback on a new line
        if record.exc_info and record.exc_info[0]:
            tb = self.formatException(record.exc_info)
            line += f"\n{DIM}{tb.rstrip()}{RESET}"

        return line


# ---------------------------------------------------------------------------
# Database handler — persists structured log records to SQLite
# ---------------------------------------------------------------------------


class DBLogHandler(logging.Handler):
    """
    Write log records to the ai_gateway.db run_logs table.

    Uses a queue + background thread to avoid blocking the caller on DB I/O.
    Falls back silently if the DB is unavailable.
    """

    def __init__(self, db_path: str, batch_size: int = 10, flush_interval: float = 2.0):
        super().__init__()
        self.db_path = db_path
        self._queue: collections.deque = collections.deque()
        self._lock = threading.Lock()
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True, name="db-log-writer")
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        if not self._running:
            return
        entry = {
            "run_id": getattr(record, "run_id", None),
            "scene_id": getattr(record, "scene_id", None),
            "step_tag": getattr(record, "step_tag", None),
            "level": record.levelname,
            "message": record.getMessage(),
            "extra_json": json.dumps(
                _ensure_serializable(getattr(record, "log_extra", None)),
                ensure_ascii=False,
            )
            if getattr(record, "log_extra", None)
            else None,
            "loc": f"{record.pathname}:{record.lineno}:{record.funcName}",
        }

        with self._lock:
            self._queue.append(entry)

    def _worker(self) -> None:
        """Background thread that drains the queue into SQLite."""
        import sqlite3

        # Ensure the table exists
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    scene_id INTEGER,
                    step_tag TEXT,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    extra_json TEXT,
                    loc TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_run_logs_run_id ON run_logs(run_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_run_logs_level ON run_logs(level)"
            )
            conn.commit()
            conn.close()
        except Exception:
            # DB not reachable — disable DB logging
            self._running = False
            return

        while self._running:
            batch = []
            with self._lock:
                while self._queue and len(batch) < self._batch_size:
                    batch.append(self._queue.popleft())

            if batch:
                try:
                    conn = sqlite3.connect(self.db_path)
                    conn.executemany(
                        """INSERT INTO run_logs
                           (run_id, scene_id, step_tag, level, message, extra_json, loc)
                           VALUES (:run_id, :scene_id, :step_tag, :level, :message,
                                   :extra_json, :loc)""",
                        batch,
                    )
                    conn.commit()
                    conn.close()
                except Exception:
                    pass  # silently drop if DB is gone

            time.sleep(self._flush_interval)

        # Final flush of remaining entries
        with self._lock:
            remaining = list(self._queue)
            self._queue.clear()
        if remaining:
            try:
                conn = sqlite3.connect(self.db_path)
                conn.executemany(
                    """INSERT INTO run_logs
                       (run_id, scene_id, step_tag, level, message, extra_json, loc)
                       VALUES (:run_id, :scene_id, :step_tag, :level, :message,
                               :extra_json, :loc)""",
                    remaining,
                )
                conn.commit()
                conn.close()
            except Exception:
                pass

    def close(self) -> None:
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=5.0)
        super().close()


# ---------------------------------------------------------------------------
# ContextLogger — main user-facing API
# ---------------------------------------------------------------------------

_CONTEXT_KEYS = ("run_id", "scene_id", "step_tag")


class ContextLogger:
    """
    Wrapper around a standard `logging.Logger` that injects context fields
    (run_id, scene_id, step_tag) into every record via a filter.

    ``bind()`` returns a *new* ContextLogger with merged context — the
    original is not mutated, so it's safe to share across threads.
    """

    def __init__(
        self,
        logger: logging.Logger,
        run_id: Optional[str] = None,
        scene_id: Optional[int] = None,
        step_tag: Optional[str] = None,
    ):
        self._logger = logger
        self.run_id = run_id
        self.scene_id = scene_id
        self.step_tag = step_tag

        # Add a filter to inject context into every LogRecord
        self._filter = _ContextFilter(run_id, scene_id, step_tag)
        self._logger.addFilter(self._filter)

    # -- bind -----------------------------------------------------------------
    def bind(
        self,
        run_id: Optional[str] = None,
        scene_id: Optional[int] = None,
        step_tag: Optional[str] = None,
    ) -> "ContextLogger":
        """Return a new ContextLogger with additional context fields merged in."""
        return ContextLogger(
            logger=self._logger,
            run_id=run_id if run_id is not None else self.run_id,
            scene_id=scene_id if scene_id is not None else self.scene_id,
            step_tag=step_tag if step_tag is not None else self.step_tag,
        )

    # -- Log methods -----------------------------------------------------------
    def debug(self, msg: str, extra: Optional[Dict[str, Any]] = None) -> None:
        self._log(logging.DEBUG, msg, extra)

    def info(self, msg: str, extra: Optional[Dict[str, Any]] = None) -> None:
        self._log(logging.INFO, msg, extra)

    def warning(self, msg: str, extra: Optional[Dict[str, Any]] = None) -> None:
        self._log(logging.WARNING, msg, extra)

    def error(self, msg: str, extra: Optional[Dict[str, Any]] = None) -> None:
        self._log(logging.ERROR, msg, extra)

    def critical(self, msg: str, extra: Optional[Dict[str, Any]] = None) -> None:
        self._log(logging.CRITICAL, msg, extra)

    def exception(self, msg: str, extra: Optional[Dict[str, Any]] = None) -> None:
        """Log at ERROR level with the current exception traceback."""
        self._log(logging.ERROR, msg, extra, exc_info=True)

    def _log(
        self,
        level: int,
        msg: str,
        extra: Optional[Dict[str, Any]],
        exc_info: bool = False,
    ) -> None:
        extra_dict = extra or {}
        # Attach extra as a record attribute so formatters can use it
        # We use the `extra` kwarg of logging.Logger._log, which merges into
        # the LogRecord __dict__.
        log_extra = {"log_extra": extra_dict} if extra_dict else {}
        self._logger.log(level, msg, extra=log_extra, exc_info=exc_info)


class _ContextFilter(logging.Filter):
    """Injects run_id / scene_id / step_tag into every LogRecord."""

    def __init__(
        self,
        run_id: Optional[str] = None,
        scene_id: Optional[int] = None,
        step_tag: Optional[str] = None,
    ):
        super().__init__()
        self.run_id = run_id
        self.scene_id = scene_id
        self.step_tag = step_tag

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = self.run_id          # type: ignore[attr-defined]
        record.scene_id = self.scene_id      # type: ignore[attr-defined]
        record.step_tag = self.step_tag      # type: ignore[attr-defined]
        return True


# ---------------------------------------------------------------------------
# Setup — wire handlers, return ready-to-use ContextLogger
# ---------------------------------------------------------------------------

# Track active DB handlers so we can shut them down
_active_db_handlers: Dict[str, DBLogHandler] = {}
_active_db_handlers_lock = threading.Lock()


def setup_logging(
    run_id: str,
    output_dir: Optional[str] = None,
    log_level: str = "INFO",
    enable_db: bool = True,
    db_path: str = "ai_gateway.db",
    console_level: Optional[str] = None,
) -> ContextLogger:
    """
    Initialise the logging system for one pipeline run.

    Args:
        run_id: Unique identifier for this pipeline run (e.g. "run_20260712_143000").
        output_dir: Directory for the ``run.log`` file. Defaults to ``output/<run_id>/``.
        log_level: Minimum level for file & DB handlers (DEBUG, INFO, WARNING, ERROR).
        enable_db: Whether to persist logs to SQLite.
        db_path: Path to the SQLite database.
        console_level: Console minimum level (defaults to *log_level*).

    Returns:
        A ContextLogger pre-bound to *run_id*.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)
    console_lvl = getattr(logging, (console_level or log_level).upper(), level)

    # Create a dedicated logger for this pipeline run (not the root logger)
    logger_name = f"storyboard.{run_id}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)  # handlers control their own levels
    logger.propagate = False        # don't bubble to root

    # Remove any existing handlers (idempotent)
    logger.handlers.clear()
    logger.filters.clear()

    # --- Console handler ---
    # Use stdout — Docker json-file driver captures both streams, but some log
    # viewers only show stdout by default, so stderr logs appear "missing".
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_lvl)
    console_handler.setFormatter(ColoredConsoleFormatter())
    logger.addHandler(console_handler)

    # --- File handler ---
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        log_file = os.path.join(output_dir, "run.log")
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(JsonFormatter())
        logger.addHandler(file_handler)

    # --- DB handler ---
    if enable_db:
        try:
            db_handler = DBLogHandler(db_path)
            db_handler.setLevel(level)
            logger.addHandler(db_handler)
            with _active_db_handlers_lock:
                _active_db_handlers[run_id] = db_handler
        except Exception:
            pass  # DB unavailable — log to console & file only

    return ContextLogger(logger=logger, run_id=run_id)


def teardown_logging(run_id: str) -> None:
    """
    Shut down logging for a pipeline run.

    Closes file handlers and stops the DB writer thread. Call this after
    the pipeline completes to release resources.
    """
    logger_name = f"storyboard.{run_id}"
    logger = logging.getLogger(logger_name)

    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    with _active_db_handlers_lock:
        db_handler = _active_db_handlers.pop(run_id, None)
    if db_handler:
        db_handler.close()

    # Remove the logger so a future run with the same ID starts fresh
    logging.Logger.manager.loggerDict.pop(logger_name, None)


def get_run_logger(run_id: str) -> Optional[ContextLogger]:
    """Retrieve an existing run logger by run_id, or None if not found."""
    logger_name = f"storyboard.{run_id}"
    logger = logging.getLogger(logger_name)
    if logger.handlers:
        return ContextLogger(logger=logger, run_id=run_id)
    return None


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------


def log_step(
    tag: Optional[str] = None,
    level: str = "INFO",
    log_args: bool = False,
) -> Callable:
    """
    Decorator that logs function entry, exit, and elapsed time.

    The decorated function must receive a ``ContextLogger`` as its first
    positional argument, OR one of its keyword arguments named ``logger``.

    Usage::

        @log_step(tag="research")
        def do_research(logger, topic: str) -> str:
            ...
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Find logger in args or kwargs
            logger = _extract_logger(args, kwargs)
            step_label = tag or func.__name__

            log = logger.bind(step_tag=step_label) if logger else None

            # Log arguments if requested
            arg_info = {}
            if log_args and log:
                arg_info = _capture_args(func, args, kwargs)
                log.debug(f"Entering {step_label}", extra={"args": arg_info})
            elif log:
                log.info(f"Entering {step_label}")

            t0 = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed = (time.perf_counter() - t0) * 1000
                if log:
                    log.info(
                        f"Completed {step_label}",
                        extra={"elapsed_ms": round(elapsed, 1)},
                    )
                return result
            except Exception as exc:
                elapsed = (time.perf_counter() - t0) * 1000
                if log:
                    log.exception(
                        f"Failed {step_label}: {exc}",
                        extra={"elapsed_ms": round(elapsed, 1)},
                    )
                raise

        return wrapper

    return decorator


def log_subprocess(tag: Optional[str] = None) -> Callable:
    """
    Decorator for functions that return a subprocess.CompletedProcess.

    Logs the command, return code, and any captured stdout/stderr.

    Usage::

        @log_subprocess(tag="ffmpeg_concat")
        def concat_videos(logger, input_files, output):
            return subprocess.run(...)
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger = _extract_logger(args, kwargs)
            step_label = tag or func.__name__

            log = logger.bind(step_tag=step_label) if logger else None

            if log:
                log.debug(f"Running subprocess: {step_label}")

            result = func(*args, **kwargs)

            if log and isinstance(result, subprocess.CompletedProcess):
                extra: Dict[str, Any] = {
                    "return_code": result.returncode,
                }
                if result.stdout:
                    extra["stdout"] = _truncate(
                        result.stdout.decode("utf-8", errors="replace"), 300
                    )
                if result.stderr:
                    extra["stderr"] = _truncate(
                        result.stderr.decode("utf-8", errors="replace"), 300
                    )

                if result.returncode == 0:
                    log.info(f"Subprocess OK: {step_label}", extra=extra)
                else:
                    log.error(
                        f"Subprocess FAILED: {step_label} (rc={result.returncode})",
                        extra=extra,
                    )

            return result

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# FFmpeg helper
# ---------------------------------------------------------------------------


@contextmanager
def ffmpeg_log_capture(
    logger: "ContextLogger",
    step_tag: str = "ffmpeg",
):
    """
    Context manager that captures FFmpeg stderr progress lines and logs them.

    Usage::

        with ffmpeg_log_capture(logger, "merge_av") as capture:
            subprocess.run(ffmpeg_cmd, stderr=capture.pipe, ...)
    """
    import io

    class _Capture:
        def __init__(self):
            self.buffer = io.StringIO()

        @property
        def pipe(self):
            import subprocess as _sp

            return _sp.PIPE

    capture = _Capture()
    t0 = time.perf_counter()

    try:
        yield capture
    finally:
        elapsed = (time.perf_counter() - t0) * 1000
        stderr_text = capture.buffer.getvalue() if hasattr(capture, "getvalue") else ""

        # Extract key frames / progress info from FFmpeg stderr
        # FFmpeg prints lines like: "frame=  123 fps= 25 q=28.0 size=    1024kB time=00:00:05.12 bitrate=1638.4kbits/s speed=1.0x"
        frame_count = None
        for line in stderr_text.splitlines() if stderr_text else []:
            if "frame=" in line:
                try:
                    frame_count = int(line.split("frame=")[1].split()[0].strip())
                except (ValueError, IndexError):
                    pass

        extra: Dict[str, Any] = {"elapsed_ms": round(elapsed, 1)}
        if frame_count is not None:
            extra["ffmpeg_frames"] = frame_count

        logger.debug(f"FFmpeg completed: {step_tag}", extra=extra)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_logger(
    args: tuple, kwargs: dict
) -> Optional[ContextLogger]:
    """Find a ContextLogger in positional args or kwargs."""
    for arg in args:
        if isinstance(arg, ContextLogger):
            return arg
    logger = kwargs.get("logger")
    if isinstance(logger, ContextLogger):
        return logger
    return None


def _capture_args(func: Callable, args: tuple, kwargs: dict) -> Dict[str, Any]:
    """Capture function arguments for debug logging, skipping the logger."""
    import inspect

    try:
        sig = inspect.signature(func)
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        params = {}
        for name, value in bound.arguments.items():
            if isinstance(value, ContextLogger):
                continue
            # Truncate large values
            s = str(value)
            params[name] = s[:100] if len(s) > 100 else s
        return params
    except Exception:
        return {}
