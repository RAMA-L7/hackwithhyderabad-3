"""One logging setup for the whole agent.

Modules call get_logger(__name__); only the CLI calls configure_logging(). Every line carries the
current session id, taken from a context variable, so layers that know nothing about sessions (the
LLM router) still log it.
"""

from __future__ import annotations

import contextvars
import logging
import sys
from contextlib import contextmanager
from typing import Iterator, TextIO

ROOT = "debugagent"
FORMAT = "  [%(name)s] session=%(session_id)s %(message)s"
_session_id: contextvars.ContextVar[str] = contextvars.ContextVar("debugagent_session_id", default="-")

logging.getLogger(ROOT).addHandler(logging.NullHandler())  # silent until the CLI configures it


class _SessionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = _session_id.get()
        return True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name if name.startswith(ROOT) else f"{ROOT}.{name}")


def configure_logging(level: int = logging.INFO, stream: TextIO | None = None) -> None:
    """Idempotent: calling it again replaces the handler (the CLI tests call it per run)."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.addFilter(_SessionFilter())
    handler.setFormatter(logging.Formatter(FORMAT))
    root = logging.getLogger(ROOT)
    root.handlers = [h for h in root.handlers if isinstance(h, logging.NullHandler)] + [handler]
    root.setLevel(level)
    root.propagate = False


@contextmanager
def session_context(session_id: str) -> Iterator[None]:
    token = _session_id.set(session_id)
    try:
        yield
    finally:
        _session_id.reset(token)
