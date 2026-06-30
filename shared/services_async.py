from django.utils import timezone

from shared.mock_price_api import MockPriceAPI
from shared.models import Product


class PriceServiceAsync:
    """Async price refresh using Django's native async ORM (aget/asave, Django 4.1+).

    Do NOT use sync_to_async wrappers here — Django 5.2 supports these ORM
    methods natively, and wrapping them would add unnecessary overhead.
    """

    @staticmethod
    async def refresh_single_product(product_id: int) -> dict:
        try:
            product = await Product.objects.aget(pk=product_id)
        except Product.DoesNotExist as exc:
            raise ValueError(f"Product {product_id} does not exist") from exc

        api_result = await MockPriceAPI.get_price_async(product.platform_identifier)

        product.price = api_result["price"]
        product.mrp = api_result["mrp"]
        product.last_refreshed_at = timezone.now()
        await product.asave(update_fields=["price", "mrp", "last_refreshed_at"])

        return {
            "product_id": product.id,
            "new_price": product.price,
            "success": True,
        }
