"""SOP catalogue pagination contract, independent from other product lists."""

from typing import Final

from ..pagination import PaginationPolicy

SOP_DEFAULT_PAGE_SIZE: Final = 4
SOP_MIN_PAGE_SIZE: Final = 1
SOP_MAX_PAGE_SIZE: Final = 50
SOP_DEFAULT_PAGE_NUMBER: Final = 1
SOP_MIN_PAGE_NUMBER: Final = 1
SOP_MAX_PAGE_NUMBER: Final = 10_000

SOP_PAGINATION: Final = PaginationPolicy(
    default_page_size=SOP_DEFAULT_PAGE_SIZE,
    min_page_size=SOP_MIN_PAGE_SIZE,
    max_page_size=SOP_MAX_PAGE_SIZE,
    default_page_number=SOP_DEFAULT_PAGE_NUMBER,
    min_page_number=SOP_MIN_PAGE_NUMBER,
    max_page_number=SOP_MAX_PAGE_NUMBER,
)

__all__ = [name for name in globals() if name.startswith("SOP_")]
