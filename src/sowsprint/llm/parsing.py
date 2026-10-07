"""Robust JSON recovery from LLM output.

Cloud models routinely wrap structured payloads in prose or markdown fences, and
sometimes emit single quotes, trailing commas or Python-style literals. Rather than
trusting ``json.loads`` on the raw string, every structured response passes through
this module. It is also used by the deterministic offline engine to read the
``<tag>...</tag>`` context blocks that nodes embed in their prompts.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_TAG_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _balanced_objects(text: str) -> list[str]:
    """Yield every balanced ``{...}`` / ``[...]`` region, string-literal aware."""
    spans: list[str] = []
    stack: list[str] = []
    start: int | None = None
    in_string = False
    quote_char = ""
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote_char:
                in_string = False
            continue

        if char in ('"', "'"):
            in_string = True
            quote_char = char
            continue

        if char in "{[":
            if not stack:
                start = index
            stack.append(char)
        elif char in "}]":
            if stack:
                stack.pop()
                if not stack and start is not None:
                    spans.append(text[start : index + 1])
                    start = None
    return spans


def _repair(candidate: str) -> str:
    """Apply conservative repairs that are safe for JSON-like output."""
    repaired = candidate.strip()
    repaired = repaired.replace("\u201c", '"').replace("\u201d", '"')
    repaired = repaired.replace("\u2018", "'").replace("\u2019", "'")
    # Trailing commas before a closing brace/bracket.
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    return repaired


def loads_lenient(text: str) -> Any | None:
    """Parse the first JSON value found in ``text``, or ``None`` if impossible."""
    if not text or not text.strip():
        return None

    candidates: list[str] = []
    for match in _FENCE_RE.finditer(text):
        candidates.append(match.group(1))
    candidates.append(text)
    candidates.extend(_balanced_objects(text))

    for candidate in candidates:
        for attempt in (candidate, _repair(candidate)):
            try:
                return json.loads(attempt)
            except (json.JSONDecodeError, ValueError):
                continue
    return None


def extract_tagged(text: str, tag: str, default: str = "") -> str:
    """Return the body of ``<tag>...</tag>``, or ``default`` when absent."""
    pattern = _TAG_RE_CACHE.get(tag)
    if pattern is None:
        pattern = re.compile(rf"<{re.escape(tag)}>\s*(.*?)\s*</{re.escape(tag)}>", re.DOTALL)
        _TAG_RE_CACHE[tag] = pattern
    match = pattern.search(text)
    return match.group(1) if match else default


def extract_tagged_json(text: str, tag: str) -> Any | None:
    """Return the parsed JSON body of a tagged block, or ``None``."""
    body = extract_tagged(text, tag, default="")
    if not body:
        return None
    return loads_lenient(body)


def render_tagged(tag: str, value: Any) -> str:
    """Wrap ``value`` in a tagged block, serialising non-strings as JSON."""
    if isinstance(value, str):
        body = value
    else:
        body = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return f"<{tag}>\n{body}\n</{tag}>"


def coerce_list(value: Any) -> list[Any]:
    """Best-effort normalisation of a scalar-or-list field into a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if "\n" in stripped:
            return [line.strip(" -•\t") for line in stripped.splitlines() if line.strip()]
        if ";" in stripped:
            return [part.strip() for part in stripped.split(";") if part.strip()]
        return [stripped]
    return [value]
