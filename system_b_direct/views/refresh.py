from shared.price_refresh_service import mapper
from shared.price_refresh_service.schemas import RefreshRequest
from system_b_direct import container
from system_b_direct.exceptions import DirectNotFound
from system_b_direct.middleware import direct_get_route, direct_post_route


@direct_post_route(RefreshRequest)
def refresh(req):
    return container.backend.direct_service.submit_batch(req.product_ids)


@direct_get_route
def job_detail(request, job_id: str):
    try:
        job = container.backend.direct_service.get_job(job_id)
    except KeyError:
        raise DirectNotFound(f"Job {job_id} not found")
    return mapper.job_redis_to_detail_response(job)


@direct_get_route
def stats(request):
    return mapper.job_stats_to_response(container.backend.direct_service.get_stats())
