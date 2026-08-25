"""安全、无 openpyxl 依赖的目标词 XLSX 读取器。

客户文件只需要文本单元格。直接读取 OOXML 可以保持服务依赖面较小，同时明确限制压缩包
大小、路径和 XML 实体，避免把上传文件当作任意 ZIP/XML 处理。
"""

from __future__ import annotations

import posixpath
import re
import stat
from collections.abc import Iterable
from io import BytesIO
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile

from lxml import etree

from .models import TargetQuery, normalize_text

_SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS = {"m": _SHEET_NS, "r": _DOC_REL_NS, "pr": _PKG_REL_NS}

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_MAX_ARCHIVE_ENTRIES = 300
_MAX_UNCOMPRESSED_BYTES = 40 * 1024 * 1024
_MAX_MEMBER_BYTES = 12 * 1024 * 1024
_MAX_QUERIES = 300

_HEADER_QUERY_RE = re.compile(
    r"(?:优化)?目标(?:关键)?词|关键(?:问|查)询|关键问题|关键词|检索词|查询词|问题库|query",
    re.IGNORECASE,
)
_HEADER_GROUP_RE = re.compile(r"^(?:产线|业务线|产品线|分类|类别|分组|方向)$", re.IGNORECASE)
_GROUP_SUFFIX_RE = re.compile(
    r"[\s\-—–_/]*(?:关键问题|关键查询|目标(?:关键)?词|关键词|检索词|查询词|问题库)$",
    re.IGNORECASE,
)
_SEQUENCE_RE = re.compile(r"^(?:序号|编号|no\.?|\d+(?:\.0+)?)$", re.IGNORECASE)
_CELL_REF_RE = re.compile(r"^([A-Z]+)\d+$")


class TargetWorkbookInvalid(ValueError):
    """上传文件不是可安全解析的目标词工作簿。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _safe_xml(payload: bytes) -> etree._Element:
    if b"<!DOCTYPE" in payload.upper():
        raise TargetWorkbookInvalid("xlsx_doctype_forbidden")
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        recover=False,
        huge_tree=False,
        remove_comments=True,
    )
    try:
        return etree.fromstring(payload, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise TargetWorkbookInvalid("xlsx_xml_invalid") from exc


def _validate_archive(archive: ZipFile) -> None:
    infos = archive.infolist()
    if len(infos) > _MAX_ARCHIVE_ENTRIES:
        raise TargetWorkbookInvalid("xlsx_too_many_entries")
    total = 0
    for info in infos:
        name = info.filename
        path = PurePosixPath(name)
        if not name or "\\" in name or path.is_absolute() or ".." in path.parts or "\x00" in name:
            raise TargetWorkbookInvalid("xlsx_archive_path_invalid")
        mode = info.external_attr >> 16
        if mode and stat.S_ISLNK(mode):
            raise TargetWorkbookInvalid("xlsx_archive_symlink_forbidden")
        if info.file_size > _MAX_MEMBER_BYTES:
            raise TargetWorkbookInvalid("xlsx_member_too_large")
        total += info.file_size
        if total > _MAX_UNCOMPRESSED_BYTES:
            raise TargetWorkbookInvalid("xlsx_uncompressed_too_large")
        if info.file_size > 1_000_000 and info.compress_size * 200 < info.file_size:
            raise TargetWorkbookInvalid("xlsx_compression_ratio_invalid")
    required = {"xl/workbook.xml", "xl/_rels/workbook.xml.rels"}
    if not required.issubset(archive.namelist()):
        raise TargetWorkbookInvalid("xlsx_workbook_missing")


def _column_number(reference: str) -> int:
    match = _CELL_REF_RE.fullmatch(reference)
    if match is None:
        return 0
    number = 0
    for char in match.group(1):
        number = number * 26 + ord(char) - ord("A") + 1
    return number


def _shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = _safe_xml(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(str(text) for text in item.xpath(".//m:t/text()", namespaces=_NS))
        for item in root.xpath("//m:si", namespaces=_NS)
    ]


def _worksheet_paths(archive: ZipFile) -> list[tuple[str, str]]:
    workbook = _safe_xml(archive.read("xl/workbook.xml"))
    relationships = _safe_xml(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        str(rel.get("Id")): str(rel.get("Target"))
        for rel in relationships.xpath("//pr:Relationship", namespaces=_NS)
        if str(rel.get("Type") or "").endswith("/worksheet")
    }
    result: list[tuple[str, str]] = []
    for sheet in workbook.xpath("//m:sheets/m:sheet", namespaces=_NS):
        if str(sheet.get("state") or "visible") != "visible":
            continue
        relationship_id = str(sheet.get(f"{{{_DOC_REL_NS}}}id") or "")
        target = targets.get(relationship_id)
        if not target:
            continue
        if target.startswith("/"):
            resolved = target.lstrip("/")
        else:
            resolved = posixpath.normpath(posixpath.join("xl", target))
        path = PurePosixPath(resolved)
        if path.is_absolute() or ".." in path.parts or resolved not in archive.namelist():
            raise TargetWorkbookInvalid("xlsx_worksheet_path_invalid")
        name = str(sheet.get("name") or "Sheet")[:80]
        result.append((name, resolved))
    if not result:
        raise TargetWorkbookInvalid("xlsx_visible_worksheet_missing")
    return result


def _cell_text(cell: etree._Element, shared: list[str]) -> str:
    cell_type = str(cell.get("t") or "")
    if cell.find(f"{{{_SHEET_NS}}}f") is not None:
        return ""  # 不执行或信任公式；目标词必须是静态文本。
    if cell_type == "inlineStr":
        parts = cell.xpath("./m:is//m:t/text()", namespaces=_NS)
        return "".join(str(part) for part in parts).strip()
    value_nodes = cell.xpath("./m:v/text()", namespaces=_NS)
    if not value_nodes:
        return ""
    raw = str(value_nodes[0])
    if cell_type == "s":
        try:
            return shared[int(raw)].strip()
        except (IndexError, ValueError):
            raise TargetWorkbookInvalid("xlsx_shared_string_invalid") from None
    return raw.strip()


def _sheet_rows(
    archive: ZipFile, path: str, shared: list[str]
) -> Iterable[tuple[int, dict[int, str]]]:
    root = _safe_xml(archive.read(path))
    for row in root.xpath("//m:sheetData/m:row", namespaces=_NS):
        try:
            row_number = int(row.get("r") or 0)
        except ValueError:
            continue
        cells: dict[int, str] = {}
        for cell in row.xpath("./m:c", namespaces=_NS):
            column = _column_number(str(cell.get("r") or ""))
            value = _cell_text(cell, shared)
            if column and value:
                cells[column] = value
        yield row_number, cells


def _group_from_header(value: str) -> str | None:
    stripped = value.strip()
    if _HEADER_GROUP_RE.fullmatch(stripped):
        return None
    match = _GROUP_SUFFIX_RE.search(stripped)
    if match is None:
        return None
    group = stripped[: match.start()].strip(" -—–_/：:")
    return group[:80] or None


def _is_sequence(value: str) -> bool:
    return _SEQUENCE_RE.fullmatch(value.strip()) is not None


def _looks_like_question_header(value: str) -> bool:
    compact = re.sub(r"\s+", "", value)
    return bool(_HEADER_QUERY_RE.search(compact)) and len(compact) <= 30


def _extract_sheet_queries(
    archive: ZipFile,
    *,
    sheet_name: str,
    path: str,
    shared: list[str],
) -> list[tuple[str, str, int, str]]:
    result: list[tuple[str, str, int, str]] = []
    current_group = "通用"
    query_column: int | None = None
    group_column: int | None = None

    for row_number, cells in _sheet_rows(archive, path, shared):
        if not cells:
            continue

        row_group: str | None = None
        header_columns: set[int] = set()
        for column, value in cells.items():
            encoded_group = _group_from_header(value)
            if encoded_group:
                current_group = encoded_group
                row_group = encoded_group
                query_column = column
                if group_column == column:
                    # 样例格式先以“产线”占位，下一行在同一列写“网空线-关键问题”；
                    # 此时该列实际是目标词列，不应把后续每条 Query 当成分组名。
                    group_column = None
                header_columns.add(column)
            elif _HEADER_GROUP_RE.fullmatch(value.strip()):
                group_column = column
                header_columns.add(column)
            elif _looks_like_question_header(value):
                query_column = column
                header_columns.add(column)
            elif _is_sequence(value):
                header_columns.add(column)

        # “序号 / 某某线-关键问题”这类分组标题行没有目标词。
        if row_group is not None or (header_columns and header_columns == set(cells)):
            continue

        if group_column is not None:
            candidate_group = cells.get(group_column, "").strip()
            if candidate_group and not _is_sequence(candidate_group):
                current_group = candidate_group[:80]

        query = cells.get(query_column or -1, "").strip()
        if not query:
            ordered = [(column, value) for column, value in sorted(cells.items()) if value.strip()]
            if ordered and _is_sequence(ordered[0][1]):
                ordered = ordered[1:]
            ordered = [
                (column, value)
                for column, value in ordered
                if column != group_column
                and not _is_sequence(value)
                and not _looks_like_question_header(value)
            ]
            if ordered:
                query = max(ordered, key=lambda item: (len(item[1]), item[0]))[1].strip()

        if not query or _is_sequence(query) or _looks_like_question_header(query):
            continue
        if len(query) < 2 or len(query) > 200:
            raise TargetWorkbookInvalid("xlsx_query_length_invalid")
        result.append((current_group, query, row_number, sheet_name))
    return result


def parse_target_queries(payload: bytes) -> list[TargetQuery]:
    """读取所有可见工作表，保留分组与工作表顺序并对目标词去重。"""
    if not payload or len(payload) > MAX_UPLOAD_BYTES:
        raise TargetWorkbookInvalid("xlsx_upload_size_invalid")
    if not payload.startswith(b"PK"):
        raise TargetWorkbookInvalid("xlsx_signature_invalid")
    try:
        archive = ZipFile(BytesIO(payload))
    except BadZipFile as exc:
        raise TargetWorkbookInvalid("xlsx_archive_invalid") from exc

    with archive:
        _validate_archive(archive)
        shared = _shared_strings(archive)
        raw: list[tuple[str, str, int, str]] = []
        for sheet_name, path in _worksheet_paths(archive):
            raw.extend(
                _extract_sheet_queries(
                    archive,
                    sheet_name=sheet_name,
                    path=path,
                    shared=shared,
                )
            )

    unique: list[tuple[str, str, int, str]] = []
    seen: set[str] = set()
    for row in raw:
        key = normalize_text(row[1])
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(row)
    if not unique:
        raise TargetWorkbookInvalid("xlsx_no_target_queries")
    if len(unique) > _MAX_QUERIES:
        raise TargetWorkbookInvalid("xlsx_too_many_target_queries")
    return [
        TargetQuery(
            query_id=f"Q{index:03d}",
            group=group,
            text=text,
            row=row_number,
            sheet=sheet_name,
        )
        for index, (group, text, row_number, sheet_name) in enumerate(unique, start=1)
    ]
