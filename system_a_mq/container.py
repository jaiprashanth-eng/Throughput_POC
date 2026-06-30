from shared.price_refresh_service.io_django import PostgresPriceRefreshRepository
from shared.price_refresh_service.repository import PriceRefreshRepository
from shared.price_refresh_service.service import (
    PriceRefreshService,
    PriceRefreshServiceImpl,
)


class Container:
    def __init__(self) -> None:
        repository: PriceRefreshRepository = PostgresPriceRefreshRepository()
        self.price_refresh_service: PriceRefreshService = PriceRefreshServiceImpl(
            repository=repository,
        )


container = Container()
