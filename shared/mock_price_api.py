import asyncio
import hashlib
import os
import random
import time
from decimal import Decimal


class MockPriceAPI:
    @classmethod
    def get_price(cls, platform_identifier: str) -> dict:
        base_latency_ms = int(os.getenv("MOCK_API_LATENCY_MS", "80"))
        jitter_ms = int(os.getenv("MOCK_API_JITTER_MS", "40"))
        failure_rate = float(os.getenv("MOCK_API_FAILURE_RATE", "0.05"))
        jitter = random.randint(-jitter_ms, jitter_ms)
        latency_ms = max(0, base_latency_ms + jitter)
        time.sleep(latency_ms / 1000.0)

        if random.random() < failure_rate:
            raise RuntimeError(f"Mock API failure for {platform_identifier}")

        digest = hashlib.sha256(platform_identifier.encode()).hexdigest()
        price_seed = int(digest[:8], 16)
        mrp_seed = int(digest[8:16], 16)

        price = Decimal(str(100 + (price_seed % 9000) / 100)).quantize(Decimal("0.01"))
        mrp = Decimal(str(float(price) + (mrp_seed % 5000) / 100)).quantize(Decimal("0.01"))

        return {"price": price, "mrp": mrp}

    @classmethod
    async def get_price_async(cls, platform_identifier: str) -> dict:
        """Async version: uses asyncio.sleep so it yields the event loop during the wait.
        time.sleep() would block all coroutines; asyncio.sleep() lets others run concurrently.
        """
        base_latency_ms = int(os.getenv("MOCK_API_LATENCY_MS", "80"))
        jitter_ms = int(os.getenv("MOCK_API_JITTER_MS", "40"))
        failure_rate = float(os.getenv("MOCK_API_FAILURE_RATE", "0.05"))
        jitter = random.randint(-jitter_ms, jitter_ms)
        latency_ms = max(0, base_latency_ms + jitter)
        await asyncio.sleep(latency_ms / 1000.0)

        if random.random() < failure_rate:
            raise RuntimeError(f"Mock API failure for {platform_identifier}")

        digest = hashlib.sha256(platform_identifier.encode()).hexdigest()
        price_seed = int(digest[:8], 16)
        mrp_seed = int(digest[8:16], 16)
        price = Decimal(str(100 + (price_seed % 9000) / 100)).quantize(Decimal("0.01"))
        mrp = Decimal(str(float(price) + (mrp_seed % 5000) / 100)).quantize(Decimal("0.01"))
        return {"price": price, "mrp": mrp}
