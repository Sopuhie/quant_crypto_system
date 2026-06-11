"""FastAPI web dashboard for the quantitative trading system."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config.settings import (
    BINANCE_SANDBOX,
    DB_PATH,
    LOG_DIR,
    TICK_INTERVAL_SEC,
    WEB_HOST,
    WEB_PORT,
)
from database.connection import DatabaseConnection
from main import QuantTradingSystem, _load_strategies_from_db
from utils.logger import get_logger, setup_logging

logger = get_logger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


class StartRequest(BaseModel):
    sandbox: bool = BINANCE_SANDBOX
    tick_interval: float = Field(default=TICK_INTERVAL_SEC, gt=0)


class SystemManager:
    """Manage QuantTradingSystem lifecycle inside the FastAPI event loop."""

    def __init__(self) -> None:
        self.system: Optional[QuantTradingSystem] = None
        self.task: Optional[asyncio.Task[None]] = None
        self.last_error: Optional[str] = None

    @property
    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def start(self, *, sandbox: bool, tick_interval: float) -> None:
        if self.is_running:
            raise HTTPException(status_code=409, detail="Trading system is already running")

        db = DatabaseConnection(DB_PATH)
        db.initialize_schema()
        strategies = _load_strategies_from_db(db)

        self.last_error = None
        self.system = QuantTradingSystem(
            strategies,
            sandbox=sandbox,
            tick_interval=tick_interval,
        )
        self.task = asyncio.create_task(self._run(), name="quant-trading-loop")
        logger.info("Trading system task started (sandbox=%s)", sandbox)

    async def _run(self) -> None:
        assert self.system is not None
        try:
            await self.system.run()
        except asyncio.CancelledError:
            logger.info("Trading system task cancelled")
            raise
        except Exception as exc:
            self.last_error = str(exc)
            logger.exception("Trading system crashed: %s", exc)
        finally:
            self.task = None

    async def stop(self) -> None:
        if not self.is_running or self.system is None:
            return

        self.system.request_shutdown()
        assert self.task is not None
        try:
            await asyncio.wait_for(self.task, timeout=30.0)
        except asyncio.TimeoutError:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.system = None
        logger.info("Trading system stopped")

    def get_status(self) -> dict[str, Any]:
        if self.system is None:
            return {
                "running": False,
                "task_alive": self.is_running,
                "last_error": self.last_error,
                "risk": {},
            }

        status = self.system.get_status()
        status["task_alive"] = self.is_running
        status["last_error"] = self.last_error
        return status


manager = SystemManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(log_dir=LOG_DIR)
    DatabaseConnection(DB_PATH).initialize_schema()
    logger.info("Web dashboard ready at http://%s:%s", WEB_HOST, WEB_PORT)
    yield
    await manager.stop()


app = FastAPI(
    title="Quant Crypto System",
    description="Binance quantitative trading dashboard",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _read_db_rows(query: str, params: tuple[Any, ...] = (), limit: int = 50) -> list[dict[str, Any]]:
    db = DatabaseConnection(DB_PATH)
    conn = db.get_connection()
    rows = conn.execute(query, (*params, limit)).fetchall()
    return [dict(row) for row in rows]


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/system/status")
async def system_status() -> dict[str, Any]:
    return manager.get_status()


@app.post("/api/system/start")
async def system_start(body: StartRequest) -> dict[str, Any]:
    await manager.start(sandbox=body.sandbox, tick_interval=body.tick_interval)
    return {"ok": True, "status": manager.get_status()}


@app.post("/api/system/stop")
async def system_stop() -> dict[str, Any]:
    await manager.stop()
    return {"ok": True, "status": manager.get_status()}


@app.post("/api/risk/reset-circuit-breaker")
async def reset_circuit_breaker() -> dict[str, Any]:
    if manager.system is None:
        raise HTTPException(status_code=400, detail="Trading system is not initialized")
    manager.system.risk.reset_circuit_breaker()
    return {"ok": True, "status": manager.get_status()}


@app.get("/api/orders")
async def recent_orders(limit: int = 20) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 100))
    return _read_db_rows(
        """
        SELECT id, strategy_name, client_order_id, exchange_order_id, symbol,
               side, order_type, price, quantity, filled_quantity, status,
               created_at, updated_at
        FROM trade_orders
        ORDER BY id DESC
        LIMIT ?
        """,
        limit=limit,
    )


@app.get("/api/klines")
async def recent_klines(symbol: Optional[str] = None, limit: int = 20) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 100))
    if symbol:
        return _read_db_rows(
            """
            SELECT symbol, interval, open_time, open, high, low, close, volume
            FROM market_kline
            WHERE symbol = ?
            ORDER BY open_time DESC
            LIMIT ?
            """,
            (symbol,),
            limit=limit,
        )
    return _read_db_rows(
        """
        SELECT symbol, interval, open_time, open, high, low, close, volume
        FROM market_kline
        ORDER BY open_time DESC
        LIMIT ?
        """,
        limit=limit,
    )


@app.get("/api/logs")
async def tail_logs(log_type: str = "info", lines: int = 80) -> dict[str, Any]:
    lines = max(10, min(lines, 500))
    log_file = LOG_DIR / ("error.log" if log_type == "error" else "info.log")
    if not log_file.exists():
        return {"log_type": log_type, "lines": []}

    content = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"log_type": log_type, "lines": content[-lines:]}
