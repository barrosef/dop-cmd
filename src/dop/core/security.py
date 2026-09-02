"""Security and redaction utilities."""

from __future__ import annotations

import re
from typing import Iterable

from .errors import SecurityViolationError

ENV_DUMP_MARKERS = ["os.environ"]

_GITHUB_TOKEN_PATTERN = re.compile(r"\bgh[pors]_[A-Za-z0-9]{10,}\b")
_PAT_TOKEN_PATTERN = re.compile(r"\bpat_[A-Za-z0-9]{10,}\b", re.IGNORECASE)
_BEARER_PATTERN = re.compile(r"\bbearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)

_ASSIGNMENT_PATTERN = re.compile(
    r"\b(TOKEN|SECRET|KEY|PASSWORD)\b\s*[:=]\s*([^\s,;]+)",
    re.IGNORECASE,
)

_JSON_PATTERN = re.compile(
    r"(\"?(?:token|secret|key|password)\"?\s*:\s*\")[^\"]+(\")",
    re.IGNORECASE,
)


def guard_text(text: str) -> None:
    for marker in ENV_DUMP_MARKERS:
        if marker in text:
            raise SecurityViolationError("Security violation: environment dump detected.")


def _mask_token_value(match: re.Match[str]) -> str:
    token = match.group(0)
    prefix = token.split("_")[0]
    return f"{prefix}_[REDACTED]"


def _mask_assignment(match: re.Match[str]) -> str:
    key = match.group(1)
    return f"{key}=***"


def _mask_json(match: re.Match[str]) -> str:
    return f"{match.group(1)}***{match.group(2)}"


def redact(text: str) -> str:
    guard_text(text)
    redacted = text
    redacted = _GITHUB_TOKEN_PATTERN.sub(_mask_token_value, redacted)
    redacted = _PAT_TOKEN_PATTERN.sub(_mask_token_value, redacted)
    redacted = _BEARER_PATTERN.sub("Bearer [REDACTED]", redacted)
    redacted = _ASSIGNMENT_PATTERN.sub(_mask_assignment, redacted)
    redacted = _JSON_PATTERN.sub(_mask_json, redacted)
    return redacted


def redact_many(lines: Iterable[str]) -> list[str]:
    return [redact(line) for line in lines]
