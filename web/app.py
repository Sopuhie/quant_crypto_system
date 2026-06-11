"""Pure stdlib HTTP dashboard serving SQLite data and static UI files."""

from __future__ import annotations

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

from config.settings import DB_PATH, WEB_HOST, WEB_PORT
from utils.analytics import TradeAnalytics
from utils.logger import get_logger, setup_logging

logger = get_logger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


class DashboardHTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        logger.debug("HTTP %s", format % args)

    def _set_headers(self, content_type: str = "application/json", status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self) -> None:
        self._set_headers(status=200)

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path

        if path == "/api/status":
            self.get_system_status()
        elif path == "/api/orders":
            self.get_trade_orders()
        elif path == "/api/positions":
            self.get_account_positions()
        elif path == "/api/strategies":
            self.get_strategy_configs()
        elif path == "/api/klines":
            self.get_market_klines()
        elif path == "/api/analytics":
            self.get_performance_analytics()
        else:
            self.serve_static_files(path)

    def serve_static_files(self, path: str) -> None:
        if path == "/" or path == "":
            file_path = STATIC_DIR / "index.html"
        elif path.startswith("/static/"):
            file_path = STATIC_DIR / path.removeprefix("/static/")
        else:
            file_path = STATIC_DIR / path.lstrip("/")

        if file_path.exists() and file_path.is_file():
            content_type = "text/html"
            if file_path.suffix == ".css":
                content_type = "text/css"
            elif file_path.suffix == ".js":
                content_type = "text/javascript"

            self._set_headers(content_type=content_type, status=200)
            with open(file_path, "rb") as f:
                self.wfile.write(f.read())
        else:
            self._set_headers(content_type="text/plain", status=404)
            self.wfile.write(b"404 Not Found")

    def get_system_status(self) -> None:
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) as cnt FROM trade_orders")
            total_orders = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) as cnt FROM strategy_config WHERE status='active'")
            active_strategies = cursor.fetchone()["cnt"]

            conn.close()

            response = {
                "status": "running",
                "database_connected": True,
                "total_orders_recorded": total_orders,
                "active_strategies_count": active_strategies,
            }
            self._set_headers()
            self.wfile.write(json.dumps(response).encode("utf-8"))
        except Exception as exc:
            self._set_headers(status=500)
            self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))

    def get_trade_orders(self) -> None:
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trade_orders ORDER BY id DESC LIMIT 50")
            rows = cursor.fetchall()
            conn.close()

            orders = [dict(row) for row in rows]
            self._set_headers()
            self.wfile.write(json.dumps(orders).encode("utf-8"))
        except Exception as exc:
            self._set_headers(status=500)
            self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))

    def get_account_positions(self) -> None:
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM account_position ORDER BY updated_at DESC")
            rows = cursor.fetchall()
            conn.close()

            positions = [dict(row) for row in rows]
            self._set_headers()
            self.wfile.write(json.dumps(positions).encode("utf-8"))
        except Exception as exc:
            self._set_headers(status=500)
            self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))

    def get_strategy_configs(self) -> None:
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM strategy_config")
            rows = cursor.fetchall()
            conn.close()

            strategies = [dict(row) for row in rows]
            self._set_headers()
            self.wfile.write(json.dumps(strategies).encode("utf-8"))
        except Exception as exc:
            self._set_headers(status=500)
            self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))

    def get_market_klines(self) -> None:
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM market_kline ORDER BY open_time DESC LIMIT 50"
            )
            rows = cursor.fetchall()
            conn.close()

            klines = [dict(row) for row in rows]
            self._set_headers()
            self.wfile.write(json.dumps(klines).encode("utf-8"))
        except Exception as exc:
            self._set_headers(status=500)
            self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))

    def get_performance_analytics(self) -> None:
        try:
            metrics = TradeAnalytics.calculate_metrics()
            self._set_headers()
            self.wfile.write(json.dumps(metrics).encode("utf-8"))
        except Exception as exc:
            self._set_headers(status=500)
            self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))


def run_server() -> None:
    setup_logging()
    server_address = (WEB_HOST, WEB_PORT)
    httpd = HTTPServer(server_address, DashboardHTTPHandler)
    logger.info(
        "Web Dashboard Server running natively on http://%s:%s",
        WEB_HOST,
        WEB_PORT,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping Web Dashboard server...")
        httpd.server_close()


if __name__ == "__main__":
    run_server()
