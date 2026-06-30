import threading

from shared.price_refresh_service.io_django import DjangoJobRepository, DjangoProductRepository
from shared.price_refresh_service.io_gateways import MockPriceGatewayImpl
from shared.price_refresh_service.service import PriceRefreshServiceImpl
from system_a_mq.service import MqDispatchService


class MqBackendComponent:
    def __init__(self) -> None:
        self.price_refresh_service: PriceRefreshServiceImpl = PriceRefreshServiceImpl(
            product_repo=DjangoProductRepository(),
            price_gateway=MockPriceGatewayImpl(),
        )
        self.mq_service: MqDispatchService = MqDispatchService(
            job_repo=DjangoJobRepository(),
        )


backend: MqBackendComponent = None  # type: ignore

_init_lock = threading.Lock()


def instantiate_mq_backend() -> None:
    global backend
    if backend is not None:
        return

    with _init_lock:
        if backend is not None:
            return

        backend = MqBackendComponent()
