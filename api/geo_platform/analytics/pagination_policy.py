"""Sampling-progress pagination contract, sized for nested answer-cell rows."""

from typing import Final

from ..pagination import PaginationPolicy

SAMPLING_PROGRESS_DEFAULT_PAGE_SIZE: Final = 4
SAMPLING_PROGRESS_MIN_PAGE_SIZE: Final = 1
SAMPLING_PROGRESS_MAX_PAGE_SIZE: Final = 25
SAMPLING_PROGRESS_DEFAULT_PAGE_NUMBER: Final = 1
SAMPLING_PROGRESS_MIN_PAGE_NUMBER: Final = 1
SAMPLING_PROGRESS_MAX_PAGE_NUMBER: Final = 10_000

SAMPLING_PROGRESS_PAGINATION: Final = PaginationPolicy(
    default_page_size=SAMPLING_PROGRESS_DEFAULT_PAGE_SIZE,
    min_page_size=SAMPLING_PROGRESS_MIN_PAGE_SIZE,
    max_page_size=SAMPLING_PROGRESS_MAX_PAGE_SIZE,
    default_page_number=SAMPLING_PROGRESS_DEFAULT_PAGE_NUMBER,
    min_page_number=SAMPLING_PROGRESS_MIN_PAGE_NUMBER,
    max_page_number=SAMPLING_PROGRESS_MAX_PAGE_NUMBER,
)

__all__ = [name for name in globals() if name.startswith("SAMPLING_PROGRESS_")]
