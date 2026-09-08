"""Main FastAPI application and service entrypoint."""

from contextlib import asynccontextmanager
import logging
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.config.settings import settings
from app.database.database import async_init_db
from app.scheduler.scheduler import scheduler_service

# Configure structured logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pr_repair_agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize DB and background scheduler."""
    logger.info("Initializing PR Repair Agent application...")
    settings.ensure_directories()
    await async_init_db()

    # Start scheduler
    scheduler_service.start(settings.SCHEDULER_INTERVAL_HOURS)
    logger.info("Service started successfully. Running in '%s' automation mode.", settings.AUTOMATION_MODE)

    yield

    logger.info("Shutting down PR Repair Agent...")
    scheduler_service.stop()


app = FastAPI(
    title="Autonomous GitHub Pull Request Repair Agent",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(api_router)

# Mount dashboard
DASHBOARD_DIR = settings.BASE_DIR / "dashboard"


@app.get("/")
async def serve_dashboard():
    """Serve web dashboard index page."""
    index_file = DASHBOARD_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "PR Repair Agent API is running. Dashboard index.html not found."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        reload=False,
    )
