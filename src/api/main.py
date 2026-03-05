import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.notifications import router as notifications_router
from src.api.runs import router as runs_router
from src.api.snipes import router as snipes_router

log = structlog.get_logger(__name__)

app = FastAPI(
    title="Market Sniper API",
    description="Autonomous deal-hunting agent API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(snipes_router)
app.include_router(runs_router)
app.include_router(notifications_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "market-sniper"}


@app.on_event("startup")
async def on_startup():
    log.info("api.startup")


@app.on_event("shutdown")
async def on_shutdown():
    from src.db.connection import close_pool
    close_pool()
    from src.agent.tools.browser import close_browser
    await close_browser()
    log.info("api.shutdown")


if __name__ == "__main__":
    uvicorn.run("src.api.main:app", host="0.0.0.0", port=8000, reload=True)
