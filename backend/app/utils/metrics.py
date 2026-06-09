import time
import threading
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# Global metrics storage
METRICS_LOCK = threading.Lock()
REQUEST_COUNTS = {}  # (path, method, status) -> count
REQUEST_LATENCIES = {}  # (path, method) -> [durations]

class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        path = request.url.path
        method = request.method

        response = await call_next(request)

        duration = time.time() - start_time
        status_code = response.status_code

        # Save metrics
        with METRICS_LOCK:
            # Update counts
            count_key = (path, method, status_code)
            REQUEST_COUNTS[count_key] = REQUEST_COUNTS.get(count_key, 0) + 1

            # Update latencies
            latency_key = (path, method)
            if latency_key not in REQUEST_LATENCIES:
                REQUEST_LATENCIES[latency_key] = []
            
            # Limit list size to avoid memory growth
            latencies = REQUEST_LATENCIES[latency_key]
            latencies.append(duration)
            if len(latencies) > 1000:
                latencies.pop(0)

        return response

def get_prometheus_metrics() -> str:
    """
    Exposes metrics in Prometheus Exposition Format.
    """
    lines = []
    with METRICS_LOCK:
        # Request Count Metric
        lines.append("# HELP http_requests_total Total HTTP Requests")
        lines.append("# TYPE http_requests_total counter")
        for (path, method, status), count in REQUEST_COUNTS.items():
            lines.append(f'http_requests_total{{path="{path}",method="{method}",status="{status}"}} {count}')

        # Request Latencies Metric
        lines.append("# HELP http_request_duration_seconds HTTP Request Duration in seconds")
        lines.append("# TYPE http_request_duration_seconds gauge")
        for (path, method), durations in REQUEST_LATENCIES.items():
            if durations:
                avg_duration = sum(durations) / len(durations)
                max_duration = max(durations)
                lines.append(f'http_request_duration_seconds{{path="{path}",method="{method}",type="average"}} {avg_duration:.6f}')
                lines.append(f'http_request_duration_seconds{{path="{path}",method="{method}",type="max"}} {max_duration:.6f}')
                lines.append(f'http_request_duration_seconds{{path="{path}",method="{method}",type="count"}} {len(durations)}')

    return "\n".join(lines)
