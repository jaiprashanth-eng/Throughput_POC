from django.utils import timezone

from shared.mock_price_api import MockPriceAPI
from shared.models import Product


class PriceService:
    @staticmethod
    def refresh_single_product(product_id: int) -> dict:
        try:
            product = Product.objects.get(pk=product_id)
        except Product.DoesNotExist as exc:
            raise ValueError(f"Product {product_id} does not exist") from exc

        api_result = MockPriceAPI.get_price(product.platform_identifier)

        product.price = api_result["price"]
        product.mrp = api_result["mrp"]
        product.last_refreshed_at = timezone.now()
        product.save(update_fields=["price", "mrp", "last_refreshed_at"])

        return {
            "product_id": product.id,
            "new_price": product.price,
            "success": True,
        }
