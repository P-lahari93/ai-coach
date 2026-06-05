# FILE: app/ai/safety_engine.py
"""
SafetyEngine — content safety checks.

MVP implementation using:
  - Keyword block list (inappropriate content, PII patterns)
  - Max length checks
  - Basic injection pattern detection

Future enhancements (v1.1+):
  - LLM-based content classification
  - PII detection using presidio or similar
  - Prompt injection detection
  - Toxicity scoring
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# Block list of keywords that should not appear in submissions
# Covers: harmful content, clear PII patterns, injection attempts
_BLOCKED_KEYWORDS = frozenset([
    # Violence and harm
    "bomb", "weapon", "explosive", "assassination",
    # Explicit content
    "pornography", "explicit sexual",
    # Injection attempts
    "ignore previous instructions", "ignore all instructions",
    "you are now", "disregard your", "forget your training",
    "act as", "pretend you are",
    # Offensive slurs
    "n****r", "f*ggot", "k**e",
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


@dataclass(frozen=True, slots=True)
class SafetyCheckResult:
    """
    Result of a content safety check.

    Attributes:
        is_safe: True when content passes all safety checks
        reason: human-readable reason when is_safe=False
        blocked_keywords: list of matched blocked keywords
    """

    is_safe: bool
    reason: str | None
    blocked_keywords: list[str] = field(default_factory=list)


class SafetyEngine:
    """
    MVP content safety engine.

    Performs basic safety checks:
      1. Blocked keyword detection
      2. Maximum length check
      3. Prompt injection pattern detection

    All checks are synchronous and run in-process. No external API calls.
    """

    def __init__(self) -> None:
        """Initialize safety engine with block list and patterns."""
        self._blocked_keywords = _BLOCKED_KEYWORDS
        self._injection_patterns = _INJECTION_PATTERNS
        self._max_length = _MAX_CONTENT_LENGTH

    async def check_content(self, text: str) -> SafetyCheckResult:
        """
        Run all safety checks against content.

        Checks are evaluated in order:
          1. Length check (fast, reject early)
          2. Keyword check
          3. Injection pattern check

        Args:
            text: content to check (user input or LLM output)

        Returns:
            SafetyCheckResult with is_safe flag and reason if blocked
        """
        # 1. Length check
        if len(text) > self._max_length:
            return SafetyCheckResult(
                is_safe=False,
                reason=(
                    f"Content exceeds maximum allowed length of "
                    f"{self._max_length:,} characters"
                ),
                blocked_keywords=[],
            )
        
        # Empty content is considered safe (let schema validation handle it)
        if not text or not text.strip():
            return SafetyCheckResult(
                is_safe=True,
                reason=None,
                blocked_keywords=[],
            )
        
        # 2. Keyword check
        text_lower = text.lower()
        matched_keywords: list[str] = []
        
        for keyword in self._blocked_keywords:
            if keyword in text_lower:
                matched_keywords.append(keyword)
        
        if matched_keywords:
            return SafetyCheckResult(
                is_safe=False,
                reason=(
                    f"Content contains blocked keywords: "
                    f"{', '.join(matched_keywords[:3])}"  # limit in message
                ),
                blocked_keywords=matched_keywords,
            )
        
        # 3. Injection pattern check
        for pattern in self._injection_patterns:
            match = pattern.search(text)
            if match:
                return SafetyCheckResult(
                    is_safe=False,
                    reason=(
                        f"Content contains prompt injection pattern: "
                        f"'{match.group(0)[:50]}'"
                    ),
                    blocked_keywords=[],
                )
        
        return SafetyCheckResult(
            is_safe=True,
            reason=None,
            blocked_keywords=[],
        )
