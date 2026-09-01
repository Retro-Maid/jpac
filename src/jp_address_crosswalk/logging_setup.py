"""Structured JSON logging (spec §68)."""

from __future__ import annotations

import logging
import sys

import structlog

_configured = False


def configure(level: str = "INFO", json_output: bool = True, force: bool = False) -> None:
    """Configure logging once.

    ``force`` exists because importing the pipeline configures logging as a side
    effect of module-level ``get_logger`` calls, which happens before the CLI
    parses its flags. Without it ``--verbose`` could never take effect.
    """
    global _configured
    if _configured and not force:
        return
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=level)
    # basicConfig is a no-op once handlers exist, so a reconfiguration has to set
    # the level itself or `--verbose` would leave the stdlib side at INFO.
    logging.getLogger().setLevel(getattr(logging, level.upper(), logging.INFO))
    renderer = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        # Logs go to stderr, never stdout. The CLI's stdout is the answer
        # (`jpac build --json | jq .passed`), and structlog's default
        # PrintLogger writes to stdout, which made that JSON unparseable.
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        # Reconfiguration must actually take effect; a cached bound logger keeps
        # the level it was first built with.
        cache_logger_on_first_use=False,
    )
    _configured = True


def get_logger(name: str):
    configure()
    return structlog.get_logger(name)


def stage_context(source: str, stage: str):
    """Bind source/stage so every event in the block is attributable."""
    return structlog.contextvars.bound_contextvars(source=source, stage=stage)
