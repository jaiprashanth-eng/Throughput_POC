from decimal import Decimal

from django.utils import timezone

from shared.models import Product
from shared.price_refresh_service import type


class PostgresPriceRefreshRepository:
    def get_product(self, product_id: int) -> Product:
        try:
            return Product.objects.get(pk=product_id)
        except Product.DoesNotExist as exc:
            raise type.ProductNotFoundException(str(product_id)) from exc

    def save_price(self, product_id: int, price: Decimal, mrp: Decimal) -> None:
        Product.objects.filter(pk=product_id).update(
            price=price,
            mrp=mrp,
            last_refreshed_at=timezone.now(),
        )
