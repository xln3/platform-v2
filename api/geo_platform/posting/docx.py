from __future__ import annotations

import base64
import html
import posixpath
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_DOCX_BYTES = 20 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 60 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 2_000
MAX_IMAGES = 100
MAX_TEXT_CHARACTERS = 800_000

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS = {"w": _W, "r": _R, "a": _A, "rel": _REL}
_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


class DocxInvalid(ValueError):
    """The supplied file is not a bounded, readable Word document."""


@dataclass(frozen=True, slots=True)
class ParsedDocx:
    filename: str
    sha256: str
    title: str
    content_text: str
    content_html: str
    image_count: int


def _safe_members(archive: ZipFile) -> dict[str, bytes]:
    members = archive.infolist()
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise DocxInvalid("docx_archive_too_many_members")
    total = 0
    payloads: dict[str, bytes] = {}
    for member in members:
        path = PurePosixPath(member.filename)
        if path.is_absolute() or ".." in path.parts or member.is_dir():
            if member.is_dir():
                continue
            raise DocxInvalid("docx_archive_path_invalid")
        total += member.file_size
        if total > MAX_UNCOMPRESSED_BYTES:
            raise DocxInvalid("docx_archive_too_large")
        if member.filename in payloads:
            raise DocxInvalid("docx_archive_duplicate_member")
        payloads[member.filename] = archive.read(member)
    return payloads


def _relationships(payloads: dict[str, bytes]) -> dict[str, str]:
    raw = payloads.get("word/_rels/document.xml.rels")
    if raw is None:
        return {}
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise DocxInvalid("docx_relationships_invalid") from exc
    relationships: dict[str, str] = {}
    for node in root.findall("rel:Relationship", _NS):
        relationship_id = node.attrib.get("Id", "")
        target = node.attrib.get("Target", "")
        target_mode = node.attrib.get("TargetMode", "")
        if not relationship_id or not target or target_mode == "External":
            continue
        normalized = posixpath.normpath(posixpath.join("word", target))
        if normalized.startswith("word/") and ".." not in PurePosixPath(normalized).parts:
            relationships[relationship_id] = normalized
    return relationships


def _image_html(
    node: ElementTree.Element,
    relationships: dict[str, str],
    payloads: dict[str, bytes],
    seen_images: set[str],
) -> tuple[str, str]:
    parts: list[str] = []
    text_markers: list[str] = []
    for blip in node.findall(".//a:blip", _NS):
        relationship_id = blip.attrib.get(f"{{{_R}}}embed", "")
        target = relationships.get(relationship_id)
        if target is None or target in seen_images:
            continue
        raw = payloads.get(target)
        suffix = PurePosixPath(target).suffix.lower()
        mime = _IMAGE_MIME.get(suffix)
        if raw is None or mime is None:
            continue
        if len(seen_images) >= MAX_IMAGES:
            raise DocxInvalid("docx_too_many_images")
        seen_images.add(target)
        encoded = base64.b64encode(raw).decode("ascii")
        parts.append(
            f'<img src="data:{mime};base64,{encoded}" alt="文档图片 {len(seen_images)}" />'
        )
        text_markers.append(f"[图片{len(seen_images)}]")
    return "".join(parts), "".join(text_markers)


def _paragraph(
    node: ElementTree.Element,
    relationships: dict[str, str],
    payloads: dict[str, bytes],
    seen_images: set[str],
) -> tuple[str, str, str]:
    html_parts: list[str] = []
    text_parts: list[str] = []
    for child in node.iter():
        if child.tag == f"{{{_W}}}t" and child.text:
            text_parts.append(child.text)
            html_parts.append(html.escape(child.text))
        elif child.tag == f"{{{_W}}}tab":
            text_parts.append("\t")
            html_parts.append("&emsp;")
        elif child.tag in {f"{{{_W}}}br", f"{{{_W}}}cr"}:
            text_parts.append("\n")
            html_parts.append("<br />")
    image_html, image_text = _image_html(node, relationships, payloads, seen_images)
    if image_html:
        html_parts.append(image_html)
        text_parts.append(image_text)
    text = "".join(text_parts).strip()
    style_node = node.find("w:pPr/w:pStyle", _NS)
    style = style_node.attrib.get(f"{{{_W}}}val", "") if style_node is not None else ""
    return text, "".join(html_parts), style


def _table(
    node: ElementTree.Element,
    relationships: dict[str, str],
    payloads: dict[str, bytes],
    seen_images: set[str],
) -> tuple[str, str]:
    text_rows: list[str] = []
    html_rows: list[str] = []
    for row in node.findall("w:tr", _NS):
        text_cells: list[str] = []
        html_cells: list[str] = []
        for cell in row.findall("w:tc", _NS):
            cell_text: list[str] = []
            cell_html: list[str] = []
            for paragraph in cell.findall("w:p", _NS):
                text, rendered, _style = _paragraph(paragraph, relationships, payloads, seen_images)
                if text:
                    cell_text.append(text)
                if rendered:
                    cell_html.append(rendered)
            text_cells.append(" ".join(cell_text))
            html_cells.append(f"<td>{'<br />'.join(cell_html)}</td>")
        text_rows.append("\t".join(text_cells))
        html_rows.append(f"<tr>{''.join(html_cells)}</tr>")
    return "\n".join(text_rows), f"<table><tbody>{''.join(html_rows)}</tbody></table>"


def parse_docx(payload: bytes, filename: str) -> ParsedDocx:
    safe_filename = PurePosixPath(filename.replace("\\", "/")).name.strip()
    if not safe_filename or not safe_filename.lower().endswith(".docx"):
        raise DocxInvalid("docx_filename_invalid")
    if not payload or len(payload) > MAX_DOCX_BYTES:
        raise DocxInvalid("docx_file_size_invalid")
    if not payload.startswith(b"PK"):
        raise DocxInvalid("docx_signature_invalid")
    try:
        with ZipFile(BytesIO(payload)) as archive:
            files = _safe_members(archive)
    except (BadZipFile, RuntimeError, OSError) as exc:
        raise DocxInvalid("docx_archive_invalid") from exc
    document = files.get("word/document.xml")
    if document is None:
        raise DocxInvalid("docx_document_missing")
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as exc:
        raise DocxInvalid("docx_document_invalid") from exc
    body = root.find("w:body", _NS)
    if body is None:
        raise DocxInvalid("docx_body_missing")
    relationships = _relationships(files)
    seen_images: set[str] = set()
    text_blocks: list[str] = []
    html_blocks: list[str] = []
    title_candidates: list[tuple[int, str]] = []
    for child in body:
        if child.tag == f"{{{_W}}}p":
            text, rendered, style = _paragraph(child, relationships, files, seen_images)
            if not text and not rendered:
                continue
            if text:
                text_blocks.append(text)
                priority = 0 if style.lower() in {"title", "标题"} else 1
                title_candidates.append((priority, text))
            style_lower = style.lower()
            if style_lower in {"title", "标题"}:
                tag = "h1"
            elif "heading1" in style_lower or style_lower in {"1", "标题1"}:
                tag = "h2"
            elif "heading2" in style_lower or style_lower in {"2", "标题2"}:
                tag = "h3"
            else:
                tag = "p"
            html_blocks.append(f"<{tag}>{rendered}</{tag}>")
        elif child.tag == f"{{{_W}}}tbl":
            text, rendered = _table(child, relationships, files, seen_images)
            if text:
                text_blocks.append(text)
            html_blocks.append(rendered)
    content_text = "\n\n".join(text_blocks).strip()
    if not content_text:
        raise DocxInvalid("docx_content_empty")
    if len(content_text) > MAX_TEXT_CHARACTERS:
        raise DocxInvalid("docx_text_too_large")
    title = min(
        enumerate(title_candidates),
        key=lambda item: (item[1][0], item[0]),
    )[1][1]
    return ParsedDocx(
        filename=safe_filename,
        sha256=sha256(payload).hexdigest(),
        title=title[:300],
        content_text=content_text,
        content_html="".join(html_blocks),
        image_count=len(seen_images),
    )
