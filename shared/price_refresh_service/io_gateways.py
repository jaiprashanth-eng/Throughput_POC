from shared.mock_price_api import MockPriceAPI


class MockPriceGatewayImpl:
    def get_price(self, platform_identifier: str) -> dict:
        return MockPriceAPI.get_price(platform_identifier)
