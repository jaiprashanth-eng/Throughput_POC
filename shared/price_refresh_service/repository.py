from decimal import Decimal
from typing import Protocol

from shared.models import Product


class PriceRefreshRepository(Protocol):
    def get_product(self, product_id: int) -> Product: ...
    def save_price(self, product_id: int, price: Decimal, mrp: Decimal) -> None: ...
