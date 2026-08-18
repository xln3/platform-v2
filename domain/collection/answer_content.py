"""Customer-readable projections derived from immutable collected answers.

The collector keeps ``response_raw`` untouched.  This module derives the
replayable, safe views used by customer reads without treating Markdown as
trusted HTML or inventing citation links that do not have a real relation.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit

RENDER_PARSER_VERSION = "answer-render-v1"

_CITATION_MARKER_RE = re.compile(r"\[citation:(\d+)\]", re.IGNORECASE)
_LEGACY_REFERENCE_HEADER_RE = re.compile(
    r"(?m)^\s{0,3}(?:#{1,6}\s*)?(?:参考来源|参考资料|来源)\s*[：:]\s*$"
)
_LEGACY_REFERENCE_ITEM_RE = re.compile(r"^\s*\d+[.)、]\s+\S+")
_LEGACY_REFERENCE_URL_RE = re.compile(r"^\s*https?://\S+\s*$", re.IGNORECASE)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_UNORDERED_RE = re.compile(r"^\s{0,3}[-+*]\s+(.+)$")
_ORDERED_RE = re.compile(r"^\s{0,3}\d+[.)]\s+(.+)$")
_TABLE_DIVIDER_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_CODE_RE = re.compile(r"`([^`\n]+)`")
_STRONG_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_INLINE_TOKEN_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)|`([^`\n]+)`|\*\*([^*\n]+)\*\*")


@dataclass(frozen=True, slots=True)
class AnswerContentProjection:
    response_raw: str
    response_markdown_normalized: str
    response_ast: list[dict[str, Any]]
    response_html_sanitized: str
    response_plain_text: str
    response_hash: str
    render_parser_version: str = RENDER_PARSER_VERSION


def project_answer_content(
    response_raw: str,
    citations: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> AnswerContentProjection:
    """Build deterministic customer views while preserving the raw response.

    Citation markers become links only when exactly one persisted relation maps
    the platform ordinal.  Missing or ambiguous markers are rendered as plain
    ``[引用 n]`` labels, never as clickable evidence.
    """

    if not isinstance(response_raw, str):
        raise TypeError("response_raw must be a string")
    normalized = _normalize_markdown(response_raw)
    normalized = _strip_legacy_reference_appendix(normalized)
    normalized = _project_citation_markers(normalized, citations)
    ast = _parse_blocks(normalized)
    plain_text = _plain_text(ast)
    return AnswerContentProjection(
        response_raw=response_raw,
        response_markdown_normalized=normalized,
        response_ast=ast,
        response_html_sanitized=_render_html(ast),
        response_plain_text=plain_text,
        response_hash=sha256(normalized.encode("utf-8")).hexdigest(),
    )


def _normalize_markdown(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = [line.rstrip() for line in value.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    output: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if not blank:
                output.append("")
            blank = True
        else:
            output.append(line)
            blank = False
    return "\n".join(output)


def _strip_legacy_reference_appendix(value: str) -> str:
    """Remove only the exact appendix shape previously synthesized by adapters."""

    matches = list(_LEGACY_REFERENCE_HEADER_RE.finditer(value))
    if not matches:
        return value
    marker = matches[-1]
    tail = value[marker.end() :].splitlines()
    meaningful = [line for line in tail if line.strip()]
    if not meaningful:
        return value
    item_count = sum(bool(_LEGACY_REFERENCE_ITEM_RE.match(line)) for line in meaningful)
    url_count = sum(bool(_LEGACY_REFERENCE_URL_RE.match(line)) for line in meaningful)
    if item_count == 0 or url_count == 0 or item_count + url_count != len(meaningful):
        return value
    return value[: marker.start()].rstrip()


def _project_citation_markers(
    value: str, citations: list[dict[str, Any]] | tuple[dict[str, Any], ...]
) -> str:
    by_platform_ordinal: dict[int, list[int]] = {}
    for citation in citations:
        platform_ordinal = citation.get("platform_ordinal")
        ordinal = citation.get("ordinal")
        if (
            isinstance(platform_ordinal, int)
            and not isinstance(platform_ordinal, bool)
            and platform_ordinal >= 0
            and isinstance(ordinal, int)
            and not isinstance(ordinal, bool)
            and ordinal >= 1
        ):
            by_platform_ordinal.setdefault(platform_ordinal, []).append(ordinal)

    def replace(match: re.Match[str]) -> str:
        platform_ordinal = int(match.group(1))
        mapped = by_platform_ordinal.get(platform_ordinal, [])
        if len(mapped) == 1:
            ordinal = mapped[0]
            return f"[{ordinal}](#citation-{ordinal})"
        return f"[引用 {platform_ordinal}]"

    return _CITATION_MARKER_RE.sub(replace, value)


def _parse_blocks(value: str) -> list[dict[str, Any]]:
    lines = value.splitlines()
    blocks: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if line.lstrip().startswith("```"):
            language = line.lstrip()[3:].strip()[:40]
            index += 1
            body: list[str] = []
            while index < len(lines) and not lines[index].lstrip().startswith("```"):
                body.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            blocks.append({"type": "code", "language": language or None, "text": "\n".join(body)})
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            blocks.append(
                {
                    "type": "heading",
                    "level": len(heading.group(1)),
                    "text": heading.group(2).strip(),
                }
            )
            index += 1
            continue
        if index + 1 < len(lines) and "|" in line and _TABLE_DIVIDER_RE.match(lines[index + 1]):
            header = _split_table_row(line)
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(_split_table_row(lines[index]))
                index += 1
            blocks.append({"type": "table", "header": header, "rows": rows})
            continue
        unordered = _UNORDERED_RE.match(line)
        if unordered:
            items: list[str] = []
            while index < len(lines) and (item := _UNORDERED_RE.match(lines[index])):
                items.append(item.group(1).strip())
                index += 1
            blocks.append({"type": "list", "ordered": False, "items": items})
            continue
        ordered = _ORDERED_RE.match(line)
        if ordered:
            items = []
            while index < len(lines) and (item := _ORDERED_RE.match(lines[index])):
                items.append(item.group(1).strip())
                index += 1
            blocks.append({"type": "list", "ordered": True, "items": items})
            continue
        if line.lstrip().startswith(">"):
            quote: list[str] = []
            while index < len(lines) and lines[index].lstrip().startswith(">"):
                quote.append(lines[index].lstrip()[1:].lstrip())
                index += 1
            blocks.append({"type": "quote", "text": "\n".join(quote)})
            continue
        paragraph = [line.strip()]
        index += 1
        while index < len(lines) and lines[index].strip() and not _starts_block(lines, index):
            paragraph.append(lines[index].strip())
            index += 1
        blocks.append({"type": "paragraph", "text": "\n".join(paragraph)})
    return blocks


def _starts_block(lines: list[str], index: int) -> bool:
    value = lines[index]
    return bool(
        value.lstrip().startswith(("```", ">"))
        or _HEADING_RE.match(value)
        or _UNORDERED_RE.match(value)
        or _ORDERED_RE.match(value)
        or (index + 1 < len(lines) and "|" in value and _TABLE_DIVIDER_RE.match(lines[index + 1]))
    )


def _split_table_row(value: str) -> list[str]:
    return [cell.strip() for cell in value.strip().strip("|").split("|")]


def _safe_link(value: str) -> str | None:
    if re.fullmatch(r"#citation-[1-9]\d*", value):
        return value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return value


def _render_inline(value: str) -> str:
    output: list[str] = []
    cursor = 0
    for match in _INLINE_TOKEN_RE.finditer(value):
        output.append(html.escape(value[cursor : match.start()]))
        label, target, code, strong = match.groups()
        if label is not None and target is not None:
            safe_target = _safe_link(target)
            if safe_target is None:
                output.append(html.escape(label))
            else:
                external = not safe_target.startswith("#")
                attributes = ' target="_blank" rel="noreferrer noopener"' if external else ""
                escaped_target = html.escape(safe_target, quote=True)
                escaped_label = html.escape(label)
                output.append(f'<a href="{escaped_target}"{attributes}>{escaped_label}</a>')
        elif code is not None:
            output.append(f"<code>{html.escape(code)}</code>")
        else:
            assert strong is not None
            output.append(f"<strong>{html.escape(strong)}</strong>")
        cursor = match.end()
    output.append(html.escape(value[cursor:]))
    return "".join(output)


def _render_html(blocks: list[dict[str, Any]]) -> str:
    output: list[str] = []
    for block in blocks:
        kind = block["type"]
        if kind == "heading":
            level = max(1, min(int(block["level"]), 6))
            output.append(f"<h{level}>{_render_inline(str(block['text']))}</h{level}>")
        elif kind == "paragraph":
            output.append(f"<p>{_render_inline(str(block['text'])).replace(chr(10), '<br>')}</p>")
        elif kind == "quote":
            output.append(f"<blockquote>{_render_inline(str(block['text']))}</blockquote>")
        elif kind == "code":
            language = block.get("language")
            css_class = (
                f' class="language-{html.escape(str(language), quote=True)}"' if language else ""
            )
            output.append(f"<pre><code{css_class}>{html.escape(str(block['text']))}</code></pre>")
        elif kind == "list":
            tag = "ol" if block.get("ordered") else "ul"
            items = "".join(f"<li>{_render_inline(str(item))}</li>" for item in block["items"])
            output.append(f"<{tag}>{items}</{tag}>")
        elif kind == "table":
            header = "".join(f"<th>{_render_inline(str(cell))}</th>" for cell in block["header"])
            rows = "".join(
                "<tr>" + "".join(f"<td>{_render_inline(str(cell))}</td>" for cell in row) + "</tr>"
                for row in block["rows"]
            )
            output.append(f"<table><thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table>")
    return "".join(output)


def _plain_inline(value: str) -> str:
    value = _LINK_RE.sub(lambda match: match.group(1), value)
    value = _CODE_RE.sub(lambda match: match.group(1), value)
    return re.sub(r"[*_]", "", value)


def _plain_text(blocks: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for block in blocks:
        kind = block["type"]
        if kind in {"heading", "paragraph", "quote", "code"}:
            lines.append(_plain_inline(str(block["text"])))
        elif kind == "list":
            lines.extend(_plain_inline(str(item)) for item in block["items"])
        elif kind == "table":
            lines.append("\t".join(_plain_inline(str(cell)) for cell in block["header"]))
            lines.extend(
                "\t".join(_plain_inline(str(cell)) for cell in row) for row in block["rows"]
            )
    return "\n".join(line for line in lines if line).strip()
