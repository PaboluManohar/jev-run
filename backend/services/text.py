"""Extract candidate text values from a goal string.

Faithful port of ``scripts/mobile-agent/text.mjs``.  Jev selects source text;
it never invents a field value.  Explicit ``--text`` values take precedence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class TextResult:
    values: list[str] = field(default_factory=list)
    source: str = "goal"
    overflow: bool = False


_STRIP_RE = re.compile(r'^["\'"\u2018([{]+|["\'"\u2019)\]},\.!?;:]+$')


def text_candidates(goal: str, supplied: list[str] | None = None) -> TextResult:
    """Return candidate text spans derived from *goal* or the explicit list.

    The logic mirrors the JS implementation exactly:
    - If *supplied* is non-empty, deduplicate and return those values.
    - Otherwise generate all 1-to-8-word substrings, strip surrounding
      punctuation, and bail with *overflow=True* after 254 candidates.
    """
    if supplied:
        seen: set[str] = set()
        unique: list[str] = []
        for text in supplied:
            if text not in seen:
                seen.add(text)
                unique.append(text)
        return TextResult(values=unique, source="supplied")

    words = list(re.finditer(r"\S+", goal))
    values: set[str] = set()
    ordered: list[str] = []
    for length in range(1, min(8, len(words)) + 1):
        for start in range(len(words) - length + 1):
            end_match = words[start + length - 1]
            raw = goal[words[start].start() : end_match.end()]
            # Strip surrounding punctuation (mirrors the JS regex).
            value = re.sub(r'^["\'\u201c\u2018([{]+', "", raw)
            value = re.sub(r'["\'\u201d\u2019)\]},\.!?;:]+$', "", value)
            value = value.strip()
            if value and value not in values:
                values.add(value)
                ordered.append(value)
            if len(values) > 254:
                return TextResult(source="goal", overflow=True)
    return TextResult(values=ordered, source="goal")
