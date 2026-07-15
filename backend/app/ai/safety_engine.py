# FILE: backend/app/ai/safety_engine.py
"""
SafetyEngine — content safety checks.

MVP implementation using:
  - Keyword block list (harmful content, slurs, clear PII patterns)
  - Max length checks
  - Basic injection pattern detection
  - Crisis / self-harm language detection (distinct category — see below)

Crisis handling is intentionally NOT the same code path as ordinary
blocked content. A message flagged as "crisis" must never be treated
like hostile/injection content — the caller (router layer) is expected
to intercept SafetyCheckResult.category == "crisis" and respond with a
supportive message + resources instead of a hard rejection. This engine
only classifies; it does not decide the HTTP response.

Future enhancements (v1.1+):
  - LLM-based content classification
  - PII detection using presidio or similar
  - Fuzzy/normalized matching to resist leetspeak & spacing bypasses
  - Toxicity scoring
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

ViolationCategory = Literal["length", "crisis", "keyword", "injection"]


# Block list of keywords that should not appear in submissions.
# Covers: harmful content, slurs (real substrings — a masked/censored
# entry like "n****r" can never match real text via substring search,
# which was the original bug here), and injection phrase fragments.
_BLOCKED_KEYWORDS = frozenset([
    # Violence and harm
    "bomb", "weapon", "explosive", "assassination",
    # Explicit content
    "pornography", "explicit sexual",
    # Injection attempts (also covered by regex below; kept here too
    # since a plain substring check is cheaper and catches simple cases
    # the regex might miss)
    "ignore previous instructions", "ignore all instructions",
    "you are now", "disregard your", "forget your training",
    "act as", "pretend you are",
    # Slurs — real substrings, not censored placeholders. A censored
    # entry never matches real input, which defeated the purpose of
    # having this list at all.
    "nigger", "nigga", "faggot", "kike", "spic", "chink", "tranny",
    "retard",
])

# Max content length (characters)
_MAX_CONTENT_LENGTH = 50_000

# Pattern for obvious prompt injection attempts
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+a", re.IGNORECASE),
    re.compile(r"forget\s+(everything|your)", re.IGNORECASE),
    re.compile(r"disregard\s+(all|your|previous)", re.IGNORECASE),
    re.compile(r"act\s+as\s+(if\s+you\s+are|a\s+different)", re.IGNORECASE),
    re.compile(r"system\s+prompt\s*:", re.IGNORECASE),
]

# Crisis / self-harm indicators. Checked BEFORE the ordinary keyword and
# injection checks, and reported as its own category — the caller must
# route this to a supportive response, not a generic rejection.
#
# This is a coarse, keyword-level MVP heuristic. It will have both false
# positives (e.g. "I could just die of embarrassment") and false
# negatives (indirect phrasing, misspellings). It is a safety net, not
# a clinical assessment — treat every match as "worth a supportive
# response," not as a diagnosis.
_CRISIS_PATTERNS = [
    re.compile(r"\bkill\s+myself\b", re.IGNORECASE),
    re.compile(r"\bwant(ing)?\s+to\s+die\b", re.IGNORECASE),
    re.compile(r"\bend(ing)?\s+my\s+life\b", re.IGNORECASE),
    re.compile(r"\bsuicid(e|al)\b", re.IGNORECASE),
    re.compile(r"\bself[\s-]?harm\b", re.IGNORECASE),
    re.compile(r"\bhurt(ing)?\s+myself\b", re.IGNORECASE),
    re.compile(r"\bno\s+reason\s+to\s+live\b", re.IGNORECASE),
    re.compile(r"\bbetter\s+off\s+dead\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+want\s+to\s+(be\s+)?(alive|live)\b", re.IGNORECASE),
]

# Static, region-agnostic fallback resources. This is a known limitation
# — the app has no locale/region field to route to a country-specific
# hotline, so this surfaces a US-centric number plus a general prompt
# to contact local emergency services. Revisit if/when user locale
# becomes available.
CRISIS_RESOURCES: list[dict[str, str]] = [
    {
        "name": "988 Suicide & Crisis Lifeline (US)",
        "contact": "Call or text 988",
        "url": "https://988lifeline.org",
    },
    {
        "name": "Crisis Text Line",
        "contact": "Text HOME to 741741",
        "url": "https://www.crisistextline.org",
    },
    {
        "name": "International Association for Suicide Prevention",
        "contact": "Directory of crisis centres worldwide",
        "url": "https://www.iasp.info/resources/Crisis_Centres/",
    },
]


@dataclass(frozen=True, slots=True)
class SafetyCheckResult:
    """
    Result of a content safety check.

    Attributes:
        is_safe: True when content passes all safety checks
        reason: human-readable reason when is_safe=False
        category: which check failed — "length" | "crisis" | "keyword" |
                  "injection". None when is_safe=True. Callers MUST
                  branch on category=="crisis" separately from the
                  other categories — see module docstring.
        blocked_keywords: list of matched blocked keywords (keyword
                  category only; empty for other categories)
    """

    is_safe: bool
    reason: str | None
    category: ViolationCategory | None = None
    blocked_keywords: list[str] = field(default_factory=list)


class SafetyEngine:
    """
    MVP content safety engine.

    Performs safety checks in this order:
      1. Length check (fast, reject early)
      2. Crisis / self-harm language check (own category — see above)
      3. Blocked keyword check
      4. Prompt injection pattern check

    All checks are synchronous and run in-process. No external API calls,
    no database access — this class is intentionally pure. Persisting a
    record of a block (audit log) is the caller's responsibility, since
    only the caller has an open UnitOfWork with the right tenant scope.
    """

    def __init__(self) -> None:
        self._blocked_keywords = _BLOCKED_KEYWORDS
        self._injection_patterns = _INJECTION_PATTERNS
        self._crisis_patterns = _CRISIS_PATTERNS
        self._max_length = _MAX_CONTENT_LENGTH

    async def check_content(self, text: str) -> SafetyCheckResult:
        """
        Run all safety checks against content.

        Args:
            text: content to check (user input or LLM output)

        Returns:
            SafetyCheckResult with is_safe flag, category, and reason.
        """
        # 1. Length check
        if len(text) > self._max_length:
            return SafetyCheckResult(
                is_safe=False,
                reason=(
                    f"Content exceeds maximum allowed length of "
                    f"{self._max_length:,} characters"
                ),
                category="length",
            )

        # Empty content is considered safe (let schema validation handle it)
        if not text or not text.strip():
            return SafetyCheckResult(is_safe=True, reason=None, category=None)

        # 2. Crisis / self-harm check — evaluated BEFORE ordinary keyword
        # / injection checks and reported under its own category, so the
        # caller can never accidentally treat it as generic blocked content.
        for pattern in self._crisis_patterns:
            if pattern.search(text):
                return SafetyCheckResult(
                    is_safe=False,
                    reason="crisis_language_detected",
                    category="crisis",
                )

        # 3. Keyword check
        text_lower = text.lower()
        matched_keywords: list[str] = [
            kw for kw in self._blocked_keywords if kw in text_lower
        ]
        if matched_keywords:
            return SafetyCheckResult(
                is_safe=False,
                reason=(
                    f"Content contains blocked keywords: "
                    f"{', '.join(matched_keywords[:3])}"
                ),
                category="keyword",
                blocked_keywords=matched_keywords,
            )

        # 4. Injection pattern check
        for pattern in self._injection_patterns:
            match = pattern.search(text)
            if match:
                return SafetyCheckResult(
                    is_safe=False,
                    reason=(
                        f"Content contains prompt injection pattern: "
                        f"'{match.group(0)[:50]}'"
                    ),
                    category="injection",
                )

        return SafetyCheckResult(is_safe=True, reason=None, category=None)