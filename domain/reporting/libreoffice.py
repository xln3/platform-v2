"""LibreOffice/UNO boundary for production-grade DOCX pagination and PDF export.

The report application calls this module directly; it does not shell out to an
operations script.  Ubuntu packages ``pyuno`` in ``/usr/lib/python3/dist-packages``
while the application runs in an isolated Python environment, so the loader adds
that distro path explicitly before importing UNO.
"""

from __future__ import annotations

import builtins
import importlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZipFile

_UNO_PATHS = (
    Path("/usr/lib/python3/dist-packages"),
    Path("/usr/lib/libreoffice/program"),
)
_REQUIRED_FONTS = (
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
)
_PYTHON_IMPORT = builtins.__import__


class ReportRuntimeDependencyError(RuntimeError):
    """The worker cannot produce a correctly paginated formal report."""


@dataclass(frozen=True, slots=True)
class ReportRuntime:
    libreoffice: str
    version: str
    fonts: tuple[str, ...]


def _load_uno() -> Any:
    for path in _UNO_PATHS:
        value = str(path)
        if path.is_dir() and value not in sys.path:
            sys.path.append(value)
    try:
        return importlib.import_module("uno")
    except (ImportError, OSError) as exc:
        raise ReportRuntimeDependencyError("libreoffice_uno_unavailable") from exc
    finally:
        # Debian's ``uno.py`` replaces ``builtins.__import__`` process-wide so
        # ``from com.sun.star ...`` can resolve UNO classes.  This boundary uses
        # only the public ``uno`` module and dynamic service APIs; retaining that
        # hook would turn later optional Python ``ModuleNotFoundError`` values
        # into plain ``ImportError`` and can break unrelated lazy imports such as
        # RapidOCR/tqdm.  Keep the report runtime isolated from the host process.
        builtins.__import__ = _PYTHON_IMPORT


def report_runtime_preflight() -> ReportRuntime:
    """Fail closed when LibreOffice, UNO, or the report's CJK fonts are missing."""

    executable = shutil.which("libreoffice")
    if executable is None:
        raise ReportRuntimeDependencyError("libreoffice_executable_unavailable")
    _load_uno()
    missing_fonts = [str(path) for path in _REQUIRED_FONTS if not path.is_file()]
    if missing_fonts:
        raise ReportRuntimeDependencyError("formal_report_fonts_unavailable")
    try:
        completed = subprocess.run(
            [executable, "--headless", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReportRuntimeDependencyError("libreoffice_preflight_failed") from exc
    if completed.returncode != 0:
        raise ReportRuntimeDependencyError("libreoffice_preflight_failed")
    version = (completed.stdout or completed.stderr).strip().splitlines()
    return ReportRuntime(
        libreoffice=executable,
        version=version[0][:160] if version else "unknown",
        fonts=tuple(str(path) for path in _REQUIRED_FONTS),
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _property(uno: Any, name: str, value: Any) -> Any:
    item = uno.createUnoStruct("com.sun.star.beans.PropertyValue")
    item.Name = name
    item.Value = value
    return item


def _visible_external_links(payload: bytes) -> tuple[tuple[str, str], ...]:
    """Read visible URL labels and targets from the input OOXML relationship graph."""

    try:
        with ZipFile(BytesIO(payload)) as archive:
            document = ET.fromstring(archive.read("word/document.xml"))
            relationships = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
    except (KeyError, ET.ParseError, OSError):
        return ()
    relationship_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    office_relationship_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    word_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    targets = {
        str(node.attrib.get("Id")): str(node.attrib.get("Target"))
        for node in relationships.findall(f"{{{relationship_ns}}}Relationship")
        if node.attrib.get("TargetMode") == "External"
        and str(node.attrib.get("Type") or "").endswith("/hyperlink")
        and str(node.attrib.get("Target") or "").startswith(("http://", "https://"))
    }
    output: list[tuple[str, str]] = []
    for link in document.iter(f"{{{word_ns}}}hyperlink"):
        relation_id = link.attrib.get(f"{{{office_relationship_ns}}}id")
        target = targets.get(str(relation_id))
        label = "".join(node.text or "" for node in link.iter(f"{{{word_ns}}}t")).strip()
        if target and label.startswith(("http://", "https://")):
            output.append((label, target))
    return tuple(output)


def _activate_visible_links(document: Any, links: tuple[tuple[str, str], ...]) -> None:
    """Work around LibreOffice 7.3 losing imported OOXML URL annotations."""

    for label, target in links:
        descriptor = document.createSearchDescriptor()
        descriptor.SearchString = label
        found = document.findFirst(descriptor)
        if found is None:
            raise ReportRuntimeDependencyError("visible_hyperlink_text_missing")
        try:
            found.setPropertyValue("HyperLinkURL", target)
            found.setPropertyValue("VisitedCharStyleName", "Visited Internet Link")
            found.setPropertyValue("UnvisitedCharStyleName", "Internet link")
        except Exception as exc:
            raise ReportRuntimeDependencyError("visible_hyperlink_activation_failed") from exc


def refresh_docx_and_export_pdf(
    payload: bytes,
    *,
    connect_timeout_seconds: float = 20,
) -> tuple[bytes, bytes]:
    """Refresh native indexes in-place and return the refreshed DOCX and PDF bytes."""

    runtime = report_runtime_preflight()
    uno = _load_uno()
    visible_links = _visible_external_links(payload)
    port = _free_port()
    with tempfile.TemporaryDirectory(prefix="geo-formal-report-") as directory:
        root = Path(directory).resolve()
        docx_path = root / "report.docx"
        pdf_path = root / "report.pdf"
        profile = root / "lo-profile"
        docx_path.write_bytes(payload)
        accept = f"socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext"
        process = subprocess.Popen(
            [
                runtime.libreoffice,
                f"-env:UserInstallation={profile.as_uri()}",
                "--headless",
                "--nologo",
                "--nodefault",
                "--nofirststartwizard",
                "--norestore",
                f"--accept={accept}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            local_context = uno.getComponentContext()
            resolver = local_context.ServiceManager.createInstanceWithContext(
                "com.sun.star.bridge.UnoUrlResolver", local_context
            )
            context = None
            deadline = time.monotonic() + connect_timeout_seconds
            while time.monotonic() < deadline:
                try:
                    context = resolver.resolve(
                        f"uno:socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext"
                    )
                    break
                except Exception:  # LibreOffice has not opened its local socket yet.
                    time.sleep(0.2)
            if context is None:
                raise ReportRuntimeDependencyError("libreoffice_uno_connection_timeout")
            desktop = context.ServiceManager.createInstanceWithContext(
                "com.sun.star.frame.Desktop", context
            )
            document = desktop.loadComponentFromURL(
                docx_path.as_uri(),
                "_blank",
                0,
                (_property(uno, "Hidden", True), _property(uno, "ReadOnly", False)),
            )
            if document is None:
                raise ReportRuntimeDependencyError("libreoffice_docx_open_failed")
            try:
                indexes = document.getDocumentIndexes()
                for index in range(indexes.getCount()):
                    indexes.getByIndex(index).update()
                _activate_visible_links(document, visible_links)
                document.store()
                filter_data = (
                    _property(uno, "UseTaggedPDF", True),
                    _property(uno, "PDFUACompliance", True),
                    _property(uno, "ExportBookmarks", True),
                    _property(uno, "ExportBookmarksToPDFDestination", True),
                    _property(uno, "ConvertOOoTargetToPDFTarget", True),
                    _property(uno, "ExportLinksRelativeFsys", False),
                )
                document.storeToURL(
                    pdf_path.as_uri(),
                    (
                        _property(uno, "FilterName", "writer_pdf_Export"),
                        _property(uno, "Overwrite", True),
                        _property(
                            uno,
                            "FilterData",
                            uno.Any("[]com.sun.star.beans.PropertyValue", filter_data),
                        ),
                    ),
                )
            finally:
                document.close(True)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if not docx_path.is_file() or docx_path.stat().st_size == 0:
            raise ReportRuntimeDependencyError("refreshed_docx_missing")
        if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
            raise ReportRuntimeDependencyError("exported_pdf_missing")
        return docx_path.read_bytes(), pdf_path.read_bytes()


__all__ = [
    "ReportRuntime",
    "ReportRuntimeDependencyError",
    "refresh_docx_and_export_pdf",
    "report_runtime_preflight",
]
