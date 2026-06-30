from typing import Protocol


class PriceGateway(Protocol):
    def get_price(self, platform_identifier: str) -> dict: ...
