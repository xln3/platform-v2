from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from geo_platform.reports import formal_review_service2
from geo_platform.reports.formal_review_service2 import _quote_text_range
from PIL import Image, ImageDraw, ImageFont

from domain.evidence.diff import BoundingBox, OcrSpan
from workflows.activities import collection
from workflows.activities.answer_dom_anchor import (
    RapidOcrEngine,
    capture_answer_evidence,
    preflight_answer_evidence_ocr,
)
from workflows.activities.collection import (
    CollectionEvidenceRef,
    _normalize_evidence_list,
    _normalize_evidence_refs,
    _persist_evidence_assets,
)


class _AnswerLocator:
    def __init__(self) -> None:
        self.evaluate_payload: dict[str, Any] | None = None
        self.screenshot_kwargs: dict[str, Any] | None = None

    def is_visible(self, *, timeout: int) -> bool:
        del timeout
        return True

    def screenshot(self, *, path: str, **kwargs: Any) -> None:
        self.screenshot_kwargs = kwargs
        Image.new("RGB", (800, 400), "white").save(path, format="PNG")

    def evaluate(self, _script: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.evaluate_payload = payload
        return {
            "rootWidth": 400,
            "rootHeight": 200,
            "blocks": [
                {"text": "前言", "x": 10, "y": 10, "width": 50, "height": 20},
                {
                    "text": "盛邦安全不如某品牌",
                    "x": 20,
                    "y": 80,
                    "width": 200,
                    "height": 30,
                },
            ],
        }


class _CanvasAnswerLocator(_AnswerLocator):
    def screenshot(self, *, path: str, **kwargs: Any) -> None:
        self.screenshot_kwargs = kwargs
        image = Image.new("RGB", (1000, 220), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", 48)
        draw.text((30, 60), "盛邦安全不如某品牌", font=font, fill="black")
        image.save(path, format="PNG")

    def evaluate(self, _script: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.evaluate_payload = payload
        # A small accessible caption must not suppress OCR for the canvas body.
        return {
            "rootWidth": 1000,
            "rootHeight": 220,
            "blocks": [{"text": "盛邦", "x": 30, "y": 60, "width": 96, "height": 60}],
        }


class _LocatorList:
    def __init__(self, answer: _AnswerLocator) -> None:
        self.answer = answer

    def all(self) -> list[_AnswerLocator]:
        return [self.answer]


class _Page:
    def __init__(self, answer: _AnswerLocator) -> None:
        self.answer = answer

    def locator(self, _selector: str) -> _LocatorList:
        return _LocatorList(self.answer)


class _NeverOcrEngine:
    version = "never-v1"

    def __init__(self) -> None:
        self.called = False

    def recognize(self, image: bytes) -> list[OcrSpan]:
        del image
        self.called = True
        raise AssertionError("OCR must not run after a successful DOM localization")


class _EmptyOcrEngine:
    version = "fixture-empty-v1"

    def __init__(self) -> None:
        self.called = False

    def recognize(self, image: bytes) -> list[OcrSpan]:
        assert image.startswith(b"\x89PNG")
        self.called = True
        return []


def test_collection_captures_clean_answer_image_and_native_dom_offsets(tmp_path: Path) -> None:
    answer_text = "前言\n盛邦安全不如某品牌\n结尾"
    locator = _AnswerLocator()
    ocr = _NeverOcrEngine()
    output = tmp_path / "answer-evidence.png"

    captured = capture_answer_evidence(
        _Page(locator),
        assistant_selectors=(".answer",),
        answer_text=answer_text,
        output_path=output,
        excluded_selectors=(".thinking",),
        ocr_engine=ocr,
    )

    assert captured is not None
    assert captured.path == output
    assert len(captured.anchors) == 2
    target = captured.anchors[1]
    assert answer_text[target["text_start"] : target["text_end"]] == "盛邦安全不如某品牌"
    assert target["bbox"] == {
        "x": 40,
        "y": 160,
        "width": 400,
        "height": 60,
        "image_width": 800,
        "image_height": 400,
        "confidence": 1.0,
        "anchor_method": "dom_text_block_v1",
    }
    assert locator.evaluate_payload is not None
    assert locator.evaluate_payload["excludedSelectors"] == [".thinking"]
    assert locator.screenshot_kwargs is not None
    assert locator.screenshot_kwargs["style"] == ".thinking{visibility:hidden!important;}"
    assert ocr.called is False


def test_controlled_canvas_executes_real_rapidocr_fallback(tmp_path: Path) -> None:
    answer_text = "盛邦安全不如某品牌"
    output = tmp_path / "canvas-answer.png"

    captured = capture_answer_evidence(
        _Page(_CanvasAnswerLocator()),
        assistant_selectors=("canvas",),
        answer_text=answer_text,
        output_path=output,
        ocr_engine=RapidOcrEngine(),
    )

    assert captured is not None
    assert len(captured.anchors) == 1
    anchor = captured.anchors[0]
    assert (anchor["text_start"], anchor["text_end"], anchor["text"]) == (
        0,
        len(answer_text),
        answer_text,
    )
    bbox = anchor["bbox"]
    assert bbox["anchor_method"] == "ocr_rapidocr_ppocrv6_v1"
    assert bbox["ocr_version"].startswith("rapidocr-3.9.2+onnxruntime-")
    assert bbox["confidence"] >= 0.9
    assert bbox["image_width"] == 1000
    assert bbox["image_height"] == 220
    assert 0 <= bbox["x"] < bbox["image_width"]
    assert 0 <= bbox["y"] < bbox["image_height"]
    assert bbox["x"] + bbox["width"] <= bbox["image_width"]
    assert bbox["y"] + bbox["height"] <= bbox["image_height"]


def test_dom_and_ocr_failure_is_closed_and_removes_candidate(tmp_path: Path) -> None:
    locator = _CanvasAnswerLocator()
    ocr = _EmptyOcrEngine()
    output = tmp_path / "unanchored.png"

    captured = capture_answer_evidence(
        _Page(locator),
        assistant_selectors=("canvas",),
        answer_text="different persisted answer",
        output_path=output,
        ocr_engine=ocr,
    )

    assert captured is None
    assert ocr.called is True
    assert not output.exists()


def test_ocr_protocol_adapter_emits_existing_ocr_span_shape() -> None:
    class _Output:
        txts = ("盛邦安全",)
        scores = (0.98,)
        boxes = ([[10, 20], [110, 20], [110, 50], [10, 50]],)

    engine = RapidOcrEngine(lambda _payload: _Output(), engine_version="fixture-v1")

    assert engine.recognize(b"image") == (
        OcrSpan(
            text="盛邦安全",
            start=0,
            end=4,
            bbox=BoundingBox(x=10, y=20, width=100, height=30),
            confidence=0.98,
        ),
    )


def test_runtime_preflight_executes_real_ocr_on_controlled_memory_png() -> None:
    version = preflight_answer_evidence_ocr(RapidOcrEngine())

    assert version == "rapidocr-3.9.2+onnxruntime-1.28.0"


def _ocr_anchor() -> dict[str, Any]:
    return {
        "text_start": 0,
        "text_end": 4,
        "text": "盛邦安全",
        "bbox": {
            "x": 1,
            "y": 2,
            "width": 30,
            "height": 10,
            "image_width": 100,
            "image_height": 100,
            "confidence": 0.96,
            "anchor_method": "ocr_rapidocr_ppocrv6_v1",
            "ocr_version": "rapidocr-3.9.2+onnxruntime-1.28.0",
        },
    }


def test_collection_anchor_contract_survives_evidence_normalization(tmp_path: Path) -> None:
    image_path = tmp_path / "answer.png"
    Image.new("RGB", (100, 100), "white").save(image_path, format="PNG")
    normalized = _normalize_evidence_list(
        [
            CollectionEvidenceRef(
                kind="answer_excerpt_screenshot",
                path=str(image_path),
                relation_type="answer_evidence_excerpt",
                mime_type="image/png",
                anchors=[_ocr_anchor()],
            )
        ],
        answer_text="盛邦安全可验证",
    )

    bbox = normalized[0].anchors[0]["bbox"]
    assert bbox["anchor_method"] == "ocr_rapidocr_ppocrv6_v1"
    assert bbox["confidence"] == 0.96
    assert bbox["ocr_version"] == "rapidocr-3.9.2+onnxruntime-1.28.0"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda anchor: anchor.update(text="伪造文字"), "does not match"),
        (
            lambda anchor: anchor["bbox"].update(x=90, width=30),
            "exceeds image dimensions",
        ),
        (
            lambda anchor: anchor["bbox"].update(image_width=200),
            "dimensions do not match",
        ),
        (lambda anchor: anchor["bbox"].pop("ocr_version"), "version is invalid"),
    ],
)
def test_collection_rejects_unverifiable_ocr_anchor(
    tmp_path: Path,
    mutate: Any,
    message: str,
) -> None:
    image_path = tmp_path / "answer.png"
    Image.new("RGB", (100, 100), "white").save(image_path, format="PNG")
    anchor = _ocr_anchor()
    mutate(anchor)
    result = SimpleNamespace(
        answer_text="盛邦安全可验证",
        screenshot_ref="",
        evidence=[
            CollectionEvidenceRef(
                kind="answer_excerpt_screenshot",
                path=str(image_path),
                relation_type="answer_evidence_excerpt",
                mime_type="image/png",
                anchors=[anchor],
            )
        ],
    )

    with pytest.raises(ValueError, match=message):
        _normalize_evidence_refs(result)


class _Mappings:
    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    def mappings(self) -> _Mappings:
        return self

    def one(self) -> dict[str, Any]:
        return self._row


class _PersistenceSession:
    def __init__(self) -> None:
        self.asset: dict[str, Any] | None = None
        self.anchor: dict[str, Any] | None = None
        self.relation: dict[str, Any] | None = None

    def execute(self, statement: Any, params: dict[str, Any]) -> Any:
        sql = str(statement)
        if "INSERT INTO evidence.evidence_asset" in sql:
            self.asset = dict(params)
            return None
        if "FROM evidence.evidence_asset WHERE pub_id" in sql:
            assert self.asset is not None
            keys = (
                "tenant_pub_id",
                "project_pub_id",
                "kind",
                "sha256",
                "object_key",
                "mime_type",
                "byte_size",
                "source_url",
                "adapter_version",
                "image_width",
                "image_height",
            )
            return _Mappings({key: self.asset[key] for key in keys})
        if "INSERT INTO evidence.evidence_anchor" in sql:
            self.anchor = {
                **params,
                "bbox": json.loads(str(params["bbox"])),
            }
            return None
        if "FROM evidence.evidence_anchor WHERE pub_id" in sql:
            assert self.anchor is not None
            keys = (
                "tenant_pub_id",
                "evidence_pub_id",
                "text_start",
                "text_end",
                "bbox",
                "quote_hash",
            )
            return _Mappings({key: self.anchor[key] for key in keys})
        if "INSERT INTO evidence.evidence_relation" in sql:
            self.relation = dict(params)
            return None
        raise AssertionError(f"unexpected SQL: {sql}")


class _ObjectStore:
    def __init__(self) -> None:
        self.payload: bytes | None = None
        self.mime_type: str | None = None
        self.ensure_calls = 0

    def ensure_bucket(self) -> None:
        self.ensure_calls += 1

    def put_redacted(self, payload: bytes, *, mime_type: str) -> Any:
        self.payload = payload
        self.mime_type = mime_type
        digest = sha256(payload).hexdigest()
        return SimpleNamespace(
            key=f"sha256/{digest}",
            sha256=digest,
            mime_type=mime_type,
            byte_size=len(payload),
            dlp_findings=(),
        )


def test_persistence_writes_cas_asset_quote_hash_and_ocr_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = tmp_path / "answer.png"
    Image.new("RGB", (100, 100), "white").save(image_path, format="PNG")
    evidence = _normalize_evidence_list(
        [
            CollectionEvidenceRef(
                kind="answer_excerpt_screenshot",
                path=str(image_path),
                relation_type="answer_evidence_excerpt",
                mime_type="image/png",
                anchors=[_ocr_anchor()],
            )
        ],
        answer_text="盛邦安全可验证",
    )
    store = _ObjectStore()
    monkeypatch.setattr(collection, "ContentAddressedObjectStore", lambda **_kwargs: store)
    monkeypatch.setattr(
        collection,
        "get_settings",
        lambda: SimpleNamespace(
            minio_endpoint="http://minio.invalid",
            minio_access_key="key",
            minio_secret_key="secret",
        ),
    )
    session = _PersistenceSession()

    _persist_evidence_assets(
        session=session,
        tenant_pub_id="tnt_test",
        project_pub_id="prj_test",
        run_pub_id="run_test",
        answer_pub_id="ans_test",
        business_key="question-1",
        adapter_version="fixture",
        evidence=evidence,
    )

    assert store.ensure_calls == 1
    assert store.payload == image_path.read_bytes()
    assert store.mime_type == "image/png"
    assert session.asset is not None
    assert session.asset["image_width"] == 100
    assert session.asset["image_height"] == 100
    assert session.asset["sha256"] == sha256(store.payload).hexdigest()
    assert session.anchor is not None
    assert session.anchor["text_start"] == 0
    assert session.anchor["text_end"] == 4
    assert session.anchor["quote_hash"] == sha256("盛邦安全".encode()).hexdigest()
    assert session.anchor["bbox"] == _ocr_anchor()["bbox"]
    assert session.relation is not None
    assert session.relation["relation_type"] == "answer_evidence_excerpt"
    assert session.relation["from_pub_id"] == "ans_test"


def test_evidence_image_dimensions_reject_declared_mime_mismatch(tmp_path: Path) -> None:
    image_path = tmp_path / "answer.png"
    Image.new("RGB", (10, 20), "white").save(image_path, format="PNG")

    with pytest.raises(ValueError, match="does not match"):
        collection._evidence_image_dimensions(image_path, "image/jpeg")


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _ReportConnection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def execute(self, _sql: str, _params: Any) -> _Rows:
        return _Rows(self._rows)


def test_report_asset_attachment_rechecks_persisted_quote_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer_text = "盛邦安全不如某品牌"
    row = {
        "answer_pub_id": "ans_test",
        "pub_id": "evd_test",
        "object_key": "sha256/asset",
        "sha256": "a" * 64,
        "mime_type": "image/png",
        "capture_time": None,
        "text_start": 0,
        "text_end": len(answer_text),
        "bbox": _ocr_anchor()["bbox"],
        "quote_hash": "0" * 64,
    }

    @contextmanager
    def _connection(_dsn: str, _tenant: str) -> Iterator[_ReportConnection]:
        yield _ReportConnection([row])

    monkeypatch.setattr(formal_review_service2, "_platform_tenant_connection", _connection)
    case = {
        "answer_pub_id": "ans_test",
        "_answer_quote_range": (0, len(answer_text)),
        "_answer_text": answer_text,
    }
    formal_review_service2._attach_answer_case_assets("dsn", "tnt_test", [case])

    assert case["answer_screenshot"] is None
    assert case.get("answer_anchor") is None


def test_report_quote_range_matches_whitespace_variants_without_guessing_pixels() -> None:
    response = "说明：盛邦安全  不如\n某品牌；仅供参考。"

    assert _quote_text_range(response, "盛邦安全 不如 某品牌") == (3, 15)
    assert _quote_text_range(response, "页面并不存在的判断") is None
