"""Convert the small, common Markdown subset used by agent replies to Slack mrkdwn."""
from __future__ import annotations

import re


HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)(?:\s+#+)?$")
RULE_RE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
CODE_SPAN_RE = re.compile(r"(`+)(.*?)\1")
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((https?://[^\s)]+)\)")
LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\((https?://[^\s)]+)\)")
STRIKE_RE = re.compile(r"~~(.+?)~~")
STRONG_RE = re.compile(r"\*\*(.+?)\*\*")


def _convert_inline(text: str) -> str:
    """Convert inline Markdown while leaving code spans literal."""
    def convert_plain(segment: str) -> str:
        segment = IMAGE_RE.sub(r"<\2|\1>", segment)
        segment = LINK_RE.sub(r"<\2|\1>", segment)
        segment = STRIKE_RE.sub(r"~\1~", segment)
        return STRONG_RE.sub(r"*\1*", segment)

    parts: list[str] = []
    cursor = 0
    for match in CODE_SPAN_RE.finditer(text):
        parts.append(convert_plain(text[cursor : match.start()]))
        parts.append(match.group(0))
        cursor = match.end()
    parts.append(convert_plain(text[cursor:]))
    return "".join(parts)


def to_mrkdwn(markdown: str) -> str:
    """Render common Markdown as Slack's text-formatting dialect.

    Slack does not implement Markdown headings or double-asterisk bold. Fenced
    code blocks are deliberately passed through unchanged so generated commands
    and examples remain exact.
    """
    converted: list[str] = []
    in_fence = False
    for line in markdown.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        ending = line[len(content) :]
        if content.lstrip().startswith("```"):
            in_fence = not in_fence
            converted.append(line)
            continue
        if in_fence:
            converted.append(line)
            continue

        heading = HEADING_RE.match(content)
        if heading:
            content = f"*{_convert_inline(heading.group(1))}*"
        elif RULE_RE.match(content):
            content = "──────────"
        else:
            content = _convert_inline(content)
        converted.append(content + ending)
    return "".join(converted)
