"""Speech text preparation and the TTS engine factory.

Kokoro (``tts_kokoro.KokoroTTSEngine``) is the only engine. This module keeps
the engine-independent pieces: turning a reply into speakable text (markdown,
URLs, numbered lists) and estimating spoken duration for echo detection.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse



# Default speaking rates for TTS estimation
DEFAULT_WPM = 200  # Default rate used in config (words per minute)
AUDIO_BUFFER_DELAY_SEC = 0.5  # Extra delay for audio buffer latency


def _estimate_tts_duration(text: str, wpm: int) -> float:
    """
    Estimate how long TTS audio will take to play.

    Args:
        text: The text being spoken
        wpm: Words per minute rate

    Returns:
        Estimated duration in seconds
    """
    # Count words (simple split on whitespace)
    words = len(text.split())

    # Calculate duration based on WPM
    if wpm <= 0:
        wpm = DEFAULT_WPM

    duration_sec = (words / wpm) * 60.0

    # Add buffer for audio latency
    return duration_sec + AUDIO_BUFFER_DELAY_SEC


def _extract_domain_description(url: str) -> tuple[str, bool]:
    """
    Extract a readable domain description from a URL.

    Returns:
        Tuple of (domain_description, is_homepage)
        - domain_description: e.g., "google.com"
        - is_homepage: True if URL points to homepage (no meaningful path)
    """
    try:
        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path.split('/')[0]

        # Remove common prefixes
        if domain.startswith('www.'):
            domain = domain[4:]

        # Check if it's a homepage (no path or just /)
        path = parsed.path.rstrip('/')
        is_homepage = not path or path == ''

        return domain, is_homepage
    except Exception:
        return url, True


_NUMBERED_MARKER_RE = re.compile(r"^\s*(\d+)[.)]\s+")


def _strip_markdown_for_speech(text: str) -> str:
    """Strip markdown formatting so TTS doesn't read syntax characters aloud.

    Small models often produce markdown (``**bold**``, bullet lists, headings)
    even when told to be conversational. Piper and similar engines read the
    syntax characters literally ("asterisk asterisk bold asterisk asterisk").
    This function removes the markup while preserving the words inside it.

    Handled:
    - Fenced code blocks ``` ```lang\\ncode\\n``` ``` → inner text only
    - Inline code ``` `x` ``` → ``x``
    - Bold ``**x**`` / ``__x__`` → ``x``
    - Italic ``*x*`` / ``_x_`` → ``x``
    - Strikethrough ``~~x~~`` → ``x``
    - Word-internal underscores (e.g. ``my_function``) are preserved so
      identifiers aren't mangled into concatenated words.
    - HTML tags ``<b>x</b>`` → ``x``
    - Leading heading markers ``# ``, ``## `` … at line start → removed
    - Setext heading underlines (``===`` / ``---`` beneath a title line) → removed
    - Leading blockquote markers ``> `` at line start → removed
    - Leading bullet markers ``- ``, ``* ``, ``+ `` at line start → removed
    - Leading numbered-list markers ``1. ``, ``2) ``: stripped only when the
      line is part of a real list — detected as ≥2 adjacent lines whose
      numbers are each ≤ 99. Prevents eating prose like "2024. The year...".
    """
    if not text:
        return text

    # Fenced code blocks: keep inner content, drop fences and language tag.
    text = re.sub(r"```[a-zA-Z0-9_-]*\n?([\s\S]*?)```", r"\1", text)

    # Inline code: keep inner content.
    text = re.sub(r"`([^`]+)`", r"\1", text)

    # Bold / strikethrough (before italic so the double-char form matches first).
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"~~([^~]+)~~", r"\1", text)

    # Italic with asterisk: single * not flanked by another *.
    text = re.sub(r"(?<!\*)\*([^*\s][^*]*?)\*(?!\*)", r"\1", text)
    # Italic with underscore: require word boundaries so we don't eat
    # underscores inside identifiers like "some_variable_name".
    text = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"\1", text)

    # HTML tags: drop tags, keep inner text. Safe here because TTS input is
    # assistant prose, not code discussing literal inequalities like "x<3".
    text = re.sub(r"<[^>]+>", "", text)

    # True list detection: a numbered line is a list item only if it's part
    # of a contiguous group of ≥2 such lines whose numbers are each ≤ 99.
    # This preserves prose like "2024. The year..." and "2023.\n2024." pairs
    # that are clearly years, not list markers.
    lines = text.split("\n")
    numbers = [
        int(m.group(1)) if (m := _NUMBERED_MARKER_RE.match(line)) else None
        for line in lines
    ]
    strip_numbered = [False] * len(lines)
    run_start: Optional[int] = None
    for i in range(len(lines) + 1):
        in_run = i < len(lines) and numbers[i] is not None and numbers[i] <= 99
        if in_run and run_start is None:
            run_start = i
        elif not in_run and run_start is not None:
            if i - run_start >= 2:
                for k in range(run_start, i):
                    strip_numbered[k] = True
            run_start = None

    cleaned: list[str] = []
    for i, line in enumerate(lines):
        # Setext heading underline: a line of only = or - (≥3 chars) directly
        # beneath a non-empty title line. Drop the underline; keep the title.
        if (
            i > 0
            and lines[i - 1].strip()
            and re.fullmatch(r"\s*(=+|-+)\s*", line)
            and len(line.strip()) >= 3
        ):
            continue
        stripped = re.sub(r"^\s*#{1,6}\s+", "", line)        # headings
        stripped = re.sub(r"^\s*>\s?", "", stripped)         # blockquotes
        stripped = re.sub(r"^\s*[-*+]\s+", "", stripped)     # bullets
        if strip_numbered[i]:
            stripped = _NUMBERED_MARKER_RE.sub("", stripped)
        cleaned.append(stripped)
    return "\n".join(cleaned)


def _preprocess_for_speech(text: str) -> str:
    """
    Preprocess text for TTS by converting links to readable descriptions and
    stripping markdown formatting.

    Handles:
    - Markdown links: [text](url) → "Link to domain.com with the text 'text'" or
      "Link to a page under domain.com with the text 'text'"
    - Raw URLs: https://domain.com → "domain.com homepage" or
      https://domain.com/path → "a page under domain.com"
    - Markdown formatting (bold, italic, code, headings, lists) → stripped so
      TTS engines don't read syntax characters (``**``, ``#``, ``-``) aloud.
    """
    # Pattern for markdown links: [text](url)
    markdown_link_pattern = r'\[([^\]]+)\]\(([^)]+)\)'

    def replace_markdown_link(match: re.Match) -> str:
        link_text = match.group(1)
        url = match.group(2)
        domain, is_homepage = _extract_domain_description(url)

        if is_homepage:
            return f"Link to {domain} homepage with the text '{link_text}'"
        else:
            return f"Link to a page under {domain} with the text '{link_text}'"

    # Replace markdown links first
    result = re.sub(markdown_link_pattern, replace_markdown_link, text)

    # Pattern for raw URLs (not already processed as markdown)
    # Matches http://, https://, and www. prefixed URLs
    raw_url_pattern = r'(?<!\()(https?://[^\s<>\[\]()]+|www\.[^\s<>\[\]()]+)(?!\))'

    def replace_raw_url(match: re.Match) -> str:
        url = match.group(1)
        # Ensure URL has protocol for parsing
        if url.startswith('www.'):
            url = 'https://' + url
        domain, is_homepage = _extract_domain_description(url)

        if is_homepage:
            return f"{domain} homepage"
        else:
            return f"a page under {domain}"

    # Replace raw URLs
    result = re.sub(raw_url_pattern, replace_raw_url, result)

    # Strip any remaining markdown so TTS doesn't read syntax aloud.
    result = _strip_markdown_for_speech(result)

    return result


def create_tts_engine(
    enabled: bool = True,
    kokoro_voice: Optional[str] = None,
    kokoro_speed: float = 1.0,
    kokoro_lang: Optional[str] = None,
    kokoro_model_path: Optional[str] = None,
    kokoro_voices_path: Optional[str] = None,
    kokoro_robot_effect: float = 0.35,
):
    """Create the Kokoro TTS engine (multilingual, CPU, J.A.R.V.I.S. voice)."""
    from .tts_kokoro import KokoroTTSEngine
    from ..voice.tts_kokoro import DEFAULT_LANG, DEFAULT_VOICE

    return KokoroTTSEngine(
        enabled=enabled,
        voice=kokoro_voice or DEFAULT_VOICE,
        speed=kokoro_speed,
        lang=kokoro_lang or DEFAULT_LANG,
        model_path=kokoro_model_path,
        voices_path=kokoro_voices_path,
        robot_strength=kokoro_robot_effect,
    )
