#!/usr/bin/env python3
"""Refresh Writer document indexes in-place and optionally export PDFs.

``python-docx`` can insert a native TOC field but does not paginate documents.  This
small system-Python helper asks LibreOffice Writer to paginate, update the TOC (including
page numbers and hyperlinks), save the DOCX, and export the matching PDF.  It is invoked
by the formal-review generator before hashes are recorded.
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import uno
from com.sun.star.beans import PropertyValue


def _property(name: str, value: Any) -> PropertyValue:
    item = PropertyValue()
    item.Name = name
    item.Value = value
    return item


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="更新 DOCX 目录并导出 PDF")
    parser.add_argument("documents", nargs="+", type=Path)
    parser.add_argument("--pdf", action="store_true", help="同时导出同名 PDF")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    documents = [path.resolve() for path in args.documents]
    for path in documents:
        if not path.is_file():
            raise FileNotFoundError(path)

    port = _free_port()
    with tempfile.TemporaryDirectory(prefix="geo-review-lo-index-") as profile_dir:
        accept = f"socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext"
        process = subprocess.Popen(
            [
                "libreoffice",
                f"-env:UserInstallation={Path(profile_dir).resolve().as_uri()}",
                "--headless",
                "--nologo",
                "--nodefault",
                "--nofirststartwizard",
                "--norestore",
                f"--accept={accept}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            local_context = uno.getComponentContext()
            resolver = local_context.ServiceManager.createInstanceWithContext(
                "com.sun.star.bridge.UnoUrlResolver", local_context
            )
            context = None
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    context = resolver.resolve(
                        f"uno:socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext"
                    )
                    break
                except Exception:  # LibreOffice has not accepted the socket yet.
                    time.sleep(0.2)
            if context is None:
                stderr = process.stderr.read() if process.stderr else ""
                raise RuntimeError(f"LibreOffice UNO connection timed out: {stderr.strip()}")

            desktop = context.ServiceManager.createInstanceWithContext(
                "com.sun.star.frame.Desktop", context
            )
            for path in documents:
                document = desktop.loadComponentFromURL(
                    path.as_uri(),
                    "_blank",
                    0,
                    (_property("Hidden", True), _property("ReadOnly", False)),
                )
                if document is None:
                    raise RuntimeError(f"LibreOffice could not open {path}")
                try:
                    indexes = document.getDocumentIndexes()
                    for index in range(indexes.getCount()):
                        indexes.getByIndex(index).update()
                    document.store()
                    if args.pdf:
                        document.storeToURL(
                            path.with_suffix(".pdf").as_uri(),
                            (
                                _property("FilterName", "writer_pdf_Export"),
                                _property("Overwrite", True),
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
