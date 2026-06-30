from typing import Protocol

from shared.mock_price_api import MockPriceAPI
from shared.price_refresh_service import type
from shared.price_refresh_service.repository import PriceRefreshRepository


class PriceRefreshService(Protocol):
    def refresh_single_product(self, product_id: int) -> type.RefreshResult: ...


class PriceRefreshServiceImpl:
    def __init__(self, repository: PriceRefreshRepository) -> None:
        self._repository = repository

    def refresh_single_product(self, product_id: int) -> type.RefreshResult:
        try:
            product = self._repository.get_product(product_id)
            price_data = MockPriceAPI.get_price(product.platform_identifier)
            self._repository.save_price(product_id, price_data["price"], price_data["mrp"])
            return type.RefreshResult(
                product_id=product_id,
                success=True,
                new_price=price_data["price"],
            )
        except Exception as exc:
            return type.RefreshResult(
                product_id=product_id,
                success=False,
                error=str(exc),
            )
