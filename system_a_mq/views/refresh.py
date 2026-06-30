from shared.price_refresh_service import mapper
from shared.price_refresh_service.schemas import RefreshRequest
from system_a_mq import container
from system_a_mq.exceptions import MqNotFound
from system_a_mq.middleware import mq_get_route, mq_post_route


@mq_post_route(RefreshRequest)
def refresh(req):
    return container.backend.mq_service.submit_batch(req.product_ids)


@mq_get_route
def job_detail(request, job_id: str):
    try:
        job = container.backend.mq_service.get_job(job_id)
    except KeyError:
        raise MqNotFound(f"Job {job_id} not found")
    return mapper.job_redis_to_detail_response(job)


@mq_get_route
def stats(request):
    return mapper.job_stats_to_response(container.backend.mq_service.get_stats())
