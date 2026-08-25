"""Service 2 evidence-row pagination contract."""

from typing import Final

from ..pagination import CursorPaginationPolicy

SERVICE2_CORPUS_DEFAULT_PAGE_SIZE: Final = 4
SERVICE2_CORPUS_MIN_PAGE_SIZE: Final = 1
SERVICE2_CORPUS_MAX_PAGE_SIZE: Final = 25

SERVICE2_CORPUS_PAGINATION: Final = CursorPaginationPolicy(
    default_page_size=SERVICE2_CORPUS_DEFAULT_PAGE_SIZE,
    min_page_size=SERVICE2_CORPUS_MIN_PAGE_SIZE,
    max_page_size=SERVICE2_CORPUS_MAX_PAGE_SIZE,
)

__all__ = [name for name in globals() if name.startswith("SERVICE2_CORPUS_")]
