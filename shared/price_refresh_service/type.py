from dataclasses import dataclass
from decimal import Decimal


@dataclass
class RefreshResult:
    product_id: int
    success: bool
    new_price: Decimal | None = None
    error: str | None = None


class PriceRefreshRepositoryException(Exception):
    pass


class PriceRefreshServiceException(Exception):
    pass


class ProductNotFoundException(PriceRefreshRepositoryException):
    pass
