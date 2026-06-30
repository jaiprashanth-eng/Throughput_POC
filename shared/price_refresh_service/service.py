from typing import Protocol

from shared.price_refresh_service.gateway import PriceGateway
from shared.price_refresh_service.repository import ProductRepository
from shared.price_refresh_service.type import RefreshResult


class PriceRefreshService(Protocol):
    def refresh_single_product(self, product_id: int) -> RefreshResult: ...


class PriceRefreshServiceImpl:
    def __init__(self, *, product_repo: ProductRepository, price_gateway: PriceGateway) -> None:
        self._product_repo = product_repo
        self._price_gateway = price_gateway

    def refresh_single_product(self, product_id: int) -> RefreshResult:
        product = self._product_repo.get_product(product_id)
        api_result = self._price_gateway.get_price(product.platform_identifier)
        self._product_repo.update_product_price(product_id, api_result["price"], api_result["mrp"])
        return RefreshResult(
            product_id=product_id,
            new_price=api_result["price"],
            success=True,
        )
