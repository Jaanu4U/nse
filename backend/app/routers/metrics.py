from fastapi import APIRouter, Response
from app.utils.metrics import get_prometheus_metrics

router = APIRouter(prefix="/metrics", tags=["Metrics & Observability"])

@router.get("")
def get_metrics():
    metrics_data = get_prometheus_metrics()
    return Response(content=metrics_data, media_type="text/plain")
