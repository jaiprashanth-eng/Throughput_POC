import threading

from shared.price_refresh_service.io_django import DjangoJobRepository, DjangoProductRepository
from shared.price_refresh_service.io_gateways import MockPriceGatewayImpl
from shared.price_refresh_service.service import PriceRefreshServiceImpl
from system_b_direct.service import DirectDispatchService


class DirectBackendComponent:
    def __init__(self) -> None:
        product_repo = DjangoProductRepository()
        job_repo = DjangoJobRepository()
        price_gateway = MockPriceGatewayImpl()

        self.price_refresh_service: PriceRefreshServiceImpl = PriceRefreshServiceImpl(
            product_repo=product_repo,
            price_gateway=price_gateway,
        )
        self.direct_service: DirectDispatchService = DirectDispatchService(
            price_refresh_service=self.price_refresh_service,
            job_repo=job_repo,
        )


backend: DirectBackendComponent = None  # type: ignore

_init_lock = threading.Lock()


def instantiate_direct_backend() -> None:
    global backend
    if backend is not None:
        return

    with _init_lock:
        if backend is not None:
            return

        backend = DirectBackendComponent()
