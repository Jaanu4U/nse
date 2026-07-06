from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import engine
from app.db_init import init_database
from app.tasks.scheduler import start_scheduler
from app.routers import auth, stocks, screener, portfolio, metrics, fii_dii, insider_trading, options_chain, market
from app.routers import kite as kite_router
from app.routers import admin as admin_router
from app.utils.rate_limit import RateLimitMiddleware
from app.utils.metrics import MetricsMiddleware
from app.utils.db_log_handler import DBLogHandler
from app.utils.log_collector import start_log_collector
from contextlib import asynccontextmanager
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/var/log/nse_backend.log"),
    ],
)
logger = logging.getLogger(__name__)

# Attach DB handler to root logger — captures ERROR/WARNING from all services
_db_handler = DBLogHandler(service="backend")
_db_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.getLogger().addHandler(_db_handler)
# Note: start_log_collector() also attaches its own INFO+ handler at startup.

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup actions
    logger.info("Starting up NSE Stock Analysis Platform...")
    try:
        # 1. Initialize Database Schemas and Partitions
        init_database()
    except Exception as e:
        logger.error(f"Critical: Database initialization failed: {e}")
        
    try:
        # 2. Start Background Scheduler
        app.state.scheduler = start_scheduler()
    except Exception as e:
        logger.error(f"Scheduler failed to start: {e}")

    try:
        # 3. Start Log Collector (Docker + system stats)
        start_log_collector()
    except Exception as e:
        logger.error(f"Log collector failed to start: {e}")
        
    yield
    
    # Shutdown actions
    logger.info("Shutting down...")
    if hasattr(app.state, "scheduler") and app.state.scheduler is not None:
        app.state.scheduler.shutdown()

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan
)

# Add Middlewares in order: Outer to Inner
# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate Limiter Middleware
app.add_middleware(RateLimitMiddleware, limit=120, window_seconds=60)

# Metrics Tracking Middleware
app.add_middleware(MetricsMiddleware)

# Mount APIRouters
app.include_router(auth.router, prefix=settings.API_V1_STR)
app.include_router(stocks.router, prefix=settings.API_V1_STR)
app.include_router(screener.router, prefix=settings.API_V1_STR)
app.include_router(portfolio.router, prefix=settings.API_V1_STR)
app.include_router(metrics.router, prefix=settings.API_V1_STR)
app.include_router(fii_dii.router, prefix=settings.API_V1_STR)
app.include_router(insider_trading.router, prefix=settings.API_V1_STR)
app.include_router(options_chain.router, prefix=settings.API_V1_STR)
app.include_router(market.router, prefix=settings.API_V1_STR)
app.include_router(kite_router.router, prefix="")  # Kite auth callback at /kite/callback
app.include_router(admin_router.router, prefix=settings.API_V1_STR)

@app.get("/")
def read_root():
    return {"message": "Welcome to the NSE Stock Analysis API", "version": "1.0.0"}
