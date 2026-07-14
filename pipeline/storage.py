"""
pipeline/storage.py
====================
The one portable-storage seam. Transforms keep running in DuckDB; where a
Gold table *lands* is the only thing that varies, so that is the only thing
this interface abstracts (anti-over-engineering — no ORM, no repository).

    LocalParquetBackend  — today's behavior: write Gold parquet to the local
                           lake; publish is a no-op. Default; keeps tests and
                           the demo fully offline.
    SupabaseBackend      — publish Gold tables to Supabase Postgres via an
                           idempotent UPSERT. Uses DuckDB `ATTACH postgres`
                           so the leakage-audited transform SQL is never
                           rewritten — publishing is just a suffix. Serving
                           connections go through the transaction pooler
                           (port 6543), mandatory on the free tier.

Backend is chosen by the `VOSS_STORAGE` env var (see pipeline.config).
Swapping Supabase → Render → local is a one-line change here, nowhere else.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:                       # avoid importing heavy deps at module load
    import duckdb
    import pandas as pd

from pipeline.config import VOSS_STORAGE

logger = logging.getLogger(__name__)


class StorageBackend(ABC):
    """Where a Gold DataFrame lands. Two operations, nothing more."""

    @abstractmethod
    def write_parquet(self, df: "pd.DataFrame", path: str | Path) -> str:
        """Persist a Gold frame to the local/lake parquet path. Returns the path."""

    @abstractmethod
    def publish_table(
        self,
        df: "pd.DataFrame",
        table: str,
        natural_key: list[str],
        sport: str,
    ) -> int:
        """Idempotent UPSERT of a Gold frame into the serving store.

        `natural_key` MUST equal the target table's UNIQUE constraint columns so
        publish and DDL can never drift. Returns rows affected (0 for no-op).
        """


class LocalParquetBackend(StorageBackend):
    """Offline default: parquet on local disk; serving publish is a logged no-op."""

    def write_parquet(self, df: "pd.DataFrame", path: str | Path) -> str:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, compression="snappy", index=False)
        logger.info("Gold parquet written: %s (%d rows)", path, len(df))
        return str(path)

    def publish_table(
        self, df: "pd.DataFrame", table: str, natural_key: list[str], sport: str
    ) -> int:
        logger.debug(
            "LocalParquetBackend.publish_table no-op for %s (%d rows, sport=%s)",
            table, len(df), sport,
        )
        return 0


class SupabaseBackend(StorageBackend):
    """Publish Gold to Supabase Postgres. Still writes the local parquet lake.

    Requires a live DuckDB connection (for `ATTACH postgres`) and the Supabase
    transaction-pooler DSN (…pooler.supabase.com:6543/postgres). Both are only
    needed when actually publishing — construction stays cheap for tests.
    """

    def __init__(self, con: "duckdb.DuckDBPyConnection", dsn_pooler: str | None = None):
        self._con = con
        self._dsn = dsn_pooler or os.getenv("SUPABASE_DB_URL", "")
        self._attached = False
        self._local = LocalParquetBackend()

    def write_parquet(self, df: "pd.DataFrame", path: str | Path) -> str:
        return self._local.write_parquet(df, path)

    def _ensure_attached(self) -> None:
        if self._attached:
            return
        if not self._dsn:
            raise RuntimeError(
                "SupabaseBackend needs a pooler DSN (SUPABASE_DB_URL / dsn_pooler)"
            )
        self._con.execute("INSTALL postgres; LOAD postgres;")
        self._con.execute(f"ATTACH '{self._dsn}' AS pg (TYPE POSTGRES)")
        self._attached = True

    def publish_table(
        self, df: "pd.DataFrame", table: str, natural_key: list[str], sport: str
    ) -> int:
        self._ensure_attached()
        self._con.register("_stage", df)
        cols = list(df.columns)
        collist = ", ".join(cols)
        setlist = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in natural_key)
        keylist = ", ".join(natural_key)
        # `updated_at` is always refreshed on conflict (audit column).
        set_clause = f"{setlist}, updated_at = now()" if setlist else "updated_at = now()"
        self._con.execute(
            f"INSERT INTO pg.{table} ({collist}) SELECT {collist} FROM _stage "
            f"ON CONFLICT ({keylist}) DO UPDATE SET {set_clause}"
        )
        self._con.unregister("_stage")
        n = len(df)
        logger.info("Published %d rows → Supabase %s (sport=%s)", n, table, sport)
        return n


def get_storage(
    backend: str | None = None,
    con: "duckdb.DuckDBPyConnection | None" = None,
    dsn_pooler: str | None = None,
) -> StorageBackend:
    """Factory. Reads VOSS_STORAGE when `backend` is not given.

    'supabase' requires a DuckDB `con` for the ATTACH-based publish path.
    """
    backend = (backend or VOSS_STORAGE).lower()
    if backend == "local":
        return LocalParquetBackend()
    if backend == "supabase":
        if con is None:
            raise ValueError("SupabaseBackend requires a DuckDB connection (`con`)")
        return SupabaseBackend(con, dsn_pooler=dsn_pooler)
    raise ValueError(f"Unknown VOSS_STORAGE backend {backend!r} (use 'local' or 'supabase')")
