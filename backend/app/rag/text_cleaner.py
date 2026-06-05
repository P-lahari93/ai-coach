# FILE: app/rag/text_cleaner.py
"""
TextCleaner — clean and normalize extracted text.

Operations:
  - Remove control characters
  - Normalize whitespace (collapse multiple spaces, standardize line breaks)
  - Deduplicate excessive blank lines
  - Optional URL removal
"""
from __future__ import annotations

import re
import unicodedata


class TextCleaner:
    """Clean and normalize extracted document text for chunking."""

    def __init__(self) -> None:
        """Initialize regex patterns for efficient text cleaning."""
        # Control characters (except newline, tab, carriage return)
        self._control_char_pattern = re.compile(
            r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]"
        )
        
        # Multiple spaces
        self._multi_space_pattern = re.compile(r" {2,}")
        
        # Multiple newlines (more than 2)
        self._multi_newline_pattern = re.compile(r"\n{3,}")
        
        # URL pattern for optional removal
        self._url_pattern = re.compile(
            r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"
        )

    def remove_control_chars(self, text: str) -> str:
        """
        Remove control characters except newline, tab, and carriage return.

        Args:
            text: input text

        Returns:
            text with control characters removed
        """
        return self._control_char_pattern.sub("", text)

    def normalize_whitespace(self, text: str) -> str:
        """
        Normalize whitespace:
          - Replace tabs with single space
          - Replace carriage returns with newlines
          - Collapse multiple spaces into single space
          - Collapse excessive newlines (>2) into double newline
          - Strip leading/trailing whitespace from lines

        Args:
            text: input text

        Returns:
            normalized text
        """
        # Replace tabs and carriage returns
        text = text.replace("\t", " ").replace("\r\n", "\n").replace("\r", "\n")
        
        # Collapse multiple spaces
        text = self._multi_space_pattern.sub(" ", text)
        
        # Strip leading/trailing whitespace from each line
        lines = [line.strip() for line in text.split("\n")]
        text = "\n".join(lines)
        
        # Collapse excessive newlines
        text = self._multi_newline_pattern.sub("\n\n", text)
        
        return text.strip()

    def remove_urls(self, text: str, replacement: str = "") -> str:
        """
        Remove or replace URLs in text.

        Args:
            text: input text
            replacement: string to replace URLs with (default: empty string)

        Returns:
            text with URLs removed or replaced
        """
        return self._url_pattern.sub(replacement, text)

    def clean(self, text: str) -> str:
        """
        Full cleaning pipeline:
          1. Remove control characters
          2. Normalize whitespace
          3. Return cleaned text

        URLs are preserved by default. Use remove_urls() separately if needed.

        Args:
            text: raw input text

        Returns:
            cleaned and normalized text

        Raises:
            ValueError: when input text is empty after cleaning
        """
        if not text or not text.strip():
            raise ValueError("Cannot clean empty or whitespace-only text")
        
        # Apply cleaning steps
        cleaned = self.remove_control_chars(text)
        cleaned = self.normalize_whitespace(cleaned)
        
        if not cleaned:
            raise ValueError("Text became empty after cleaning")
        
        return cleaned
