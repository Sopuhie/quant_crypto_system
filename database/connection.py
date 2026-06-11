"""SQLite connection management with WAL mode and transaction helpers."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from queue import Empty, Queue
from typing import Generator, Iterator, Optional

from database.models import DDL_STATEMENTS
from utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "quant.db"

_WAL_PRAGMAS = (
    ("journal_mode", "WAL"),
    ("synchronous", "NORMAL"),
    ("foreign_keys", "ON"),
    ("busy_timeout", "5000"),
)


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    for key, value in _WAL_PRAGMAS:
        conn.execute(f"PRAGMA {key}={value};")


def _configure_connection(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)


class DatabaseConnection:
    """Thread-local SQLite connections with WAL enabled."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._local = threading.local()

    def _create_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            isolation_level=None,
        )
        _configure_connection(conn)
        logger.debug("Opened SQLite connection: %s", self.db_path)
        return conn

    def get_connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "connection", None)
        if conn is None:
            conn = self._create_connection()
            self._local.connection = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "connection", None)
        if conn is not None:
            conn.close()
            self._local.connection = None
            logger.debug("Closed SQLite connection: %s", self.db_path)

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        conn = self.get_connection()
        conn.execute("BEGIN IMMEDIATE;")
        try:
            yield conn
            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise

    def initialize_schema(self) -> None:
        conn = self.get_connection()
        for ddl in DDL_STATEMENTS:
            conn.execute(ddl)
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        logger.info("Database schema initialized at %s (journal_mode=%s)", self.db_path, journal_mode)


class ConnectionPool:
    """Simple bounded pool for dedicated writer or worker threads."""

    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
        pool_size: int = 4,
    ) -> None:
        self.db_path = Path(db_path)
        self.pool_size = pool_size
        self._pool: Queue[sqlite3.Connection] = Queue(maxsize=pool_size)
        self._lock = threading.Lock()
        self._closed = False

    def _new_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            isolation_level=None,
        )
        _configure_connection(conn)
        return conn

    def initialize(self) -> None:
        with self._lock:
            while not self._pool.full():
                self._pool.put(self._new_connection())

    @contextmanager
    def acquire(self, timeout: Optional[float] = 5.0) -> Iterator[sqlite3.Connection]:
        if self._closed:
            raise RuntimeError("Connection pool is closed")

        if self._pool.empty() and self._pool.qsize() < self.pool_size:
            with self._lock:
                if self._pool.qsize() < self.pool_size:
                    self._pool.put(self._new_connection())

        try:
            conn = self._pool.get(timeout=timeout)
        except Empty as exc:
            raise TimeoutError("Timed out waiting for database connection") from exc

        try:
            yield conn
        finally:
            if not self._closed:
                self._pool.put(conn)

    @contextmanager
    def transaction(self, timeout: Optional[float] = 5.0) -> Iterator[sqlite3.Connection]:
        with self.acquire(timeout=timeout) as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                yield conn
                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    def close_all(self) -> None:
        with self._lock:
            self._closed = True
            while not self._pool.empty():
                conn = self._pool.get_nowait()
                conn.close()
