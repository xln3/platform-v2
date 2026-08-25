"""Collection-run pagination contracts for public numbered and internal cursor APIs."""

from typing import Final

from ..pagination import CursorPaginationPolicy, PaginationPolicy

COLLECTION_RUNS_DEFAULT_PAGE_SIZE: Final = 4
COLLECTION_RUNS_MIN_PAGE_SIZE: Final = 1
COLLECTION_RUNS_MAX_PAGE_SIZE: Final = 50
COLLECTION_RUNS_DEFAULT_PAGE_NUMBER: Final = 1
COLLECTION_RUNS_MIN_PAGE_NUMBER: Final = 1
COLLECTION_RUNS_MAX_PAGE_NUMBER: Final = 20_000

COLLECTION_RUNS_PAGINATION: Final = PaginationPolicy(
    default_page_size=COLLECTION_RUNS_DEFAULT_PAGE_SIZE,
    min_page_size=COLLECTION_RUNS_MIN_PAGE_SIZE,
    max_page_size=COLLECTION_RUNS_MAX_PAGE_SIZE,
    default_page_number=COLLECTION_RUNS_DEFAULT_PAGE_NUMBER,
    min_page_number=COLLECTION_RUNS_MIN_PAGE_NUMBER,
    max_page_number=COLLECTION_RUNS_MAX_PAGE_NUMBER,
)

COLLECTION_RUNS_CURSOR_DEFAULT_PAGE_SIZE: Final = 50
COLLECTION_RUNS_CURSOR_MIN_PAGE_SIZE: Final = 1
COLLECTION_RUNS_CURSOR_MAX_PAGE_SIZE: Final = 100
COLLECTION_RUNS_CURSOR_PAGINATION: Final = CursorPaginationPolicy(
    default_page_size=COLLECTION_RUNS_CURSOR_DEFAULT_PAGE_SIZE,
    min_page_size=COLLECTION_RUNS_CURSOR_MIN_PAGE_SIZE,
    max_page_size=COLLECTION_RUNS_CURSOR_MAX_PAGE_SIZE,
)

__all__ = [name for name in globals() if name.startswith("COLLECTION_RUNS_")]
