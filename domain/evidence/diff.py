from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher, unified_diff
from hashlib import sha256
from typing import Protocol


@dataclass(frozen=True, slots=True)
class BoundingBox:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if min(self.x, self.y, self.width, self.height) < 0:
            raise ValueError("bounding-box values must be non-negative")
        if self.width == 0 or self.height == 0:
            raise ValueError("bounding-box area must be non-zero")


@dataclass(frozen=True, slots=True)
class OcrSpan:
    text: str
    start: int
    end: int
    bbox: BoundingBox
    confidence: float


class OcrEngine(Protocol):
    @property
    def version(self) -> str: ...

    def recognize(self, image: bytes) -> Sequence[OcrSpan]: ...


@dataclass(frozen=True, slots=True)
class EvidenceDiff:
    unified_text_diff: str
    text_similarity: float
    visual_similarity: float | None
    before_hash: str
    after_hash: str


def validate_ocr_spans(text: str, spans: Sequence[OcrSpan]) -> None:
    previous_end = 0
    for span in spans:
        if span.start < previous_end or span.end <= span.start or span.end > len(text):
            raise ValueError("OCR spans must be ordered, non-overlapping and within text")
        if text[span.start : span.end] != span.text:
            raise ValueError("OCR span text does not match its text interval")
        if not 0 <= span.confidence <= 1:
            raise ValueError("OCR confidence must be between 0 and 1")
        previous_end = span.end


def compare_evidence(
    before_text: str,
    after_text: str,
    *,
    before_perceptual_hash: str | None = None,
    after_perceptual_hash: str | None = None,
) -> EvidenceDiff:
    text_diff = "\n".join(
        unified_diff(
            before_text.splitlines(),
            after_text.splitlines(),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )
    visual = None
    if before_perceptual_hash is not None and after_perceptual_hash is not None:
        if len(before_perceptual_hash) != len(after_perceptual_hash):
            raise ValueError("perceptual hashes must have equal length")
        distance = sum(
            a != b for a, b in zip(before_perceptual_hash, after_perceptual_hash, strict=False)
        )
        visual = 1 - distance / len(before_perceptual_hash) if before_perceptual_hash else 1.0
    return EvidenceDiff(
        unified_text_diff=text_diff,
        text_similarity=SequenceMatcher(None, before_text, after_text).ratio(),
        visual_similarity=visual,
        before_hash=sha256(before_text.encode()).hexdigest(),
        after_hash=sha256(after_text.encode()).hexdigest(),
    )
