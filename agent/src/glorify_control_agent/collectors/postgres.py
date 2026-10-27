"""Read-only PostgreSQL monitoring and the daily top-30 SQL statement snapshot."""

from datetime import datetime, timezone

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


class PostgresCollector:
    def __init__(self, settings):
        self.settings = settings

    def connect(self, dbname=None):
        kwargs = {
            "connect_timeout": self.settings.postgres_timeout,
            "autocommit": True,
            "row_factory": dict_row,
            "options": f"-c default_transaction_read_only=on -c statement_timeout={self.settings.postgres_timeout * 1000} -c lock_timeout=1000",
        }
        if dbname:
            kwargs["dbname"] = dbname
        return psycopg.connect(self.settings.postgres_dsn.get_secret_value(), **kwargs)

    def connections(self):
        if not self.settings.postgres_dsn:
            return None
        sample = {"timestamp": datetime.now(timezone.utc).isoformat(), "available": False, "connections": []}
        try:
            with self.connect() as conn:
                sample["connections"] = conn.execute("""
                    SELECT datname AS database,
                           count(*) AS connections,
                           count(*) FILTER (WHERE state = 'active') AS active,
                           count(*) FILTER (WHERE state = 'idle') AS idle
                    FROM pg_stat_activity
                    WHERE backend_type = 'client backend'
                    GROUP BY datname
                    ORDER BY connections DESC
                """).fetchall()
            sample["available"] = True
        except Exception:
            pass
        return sample

    @staticmethod
    def query(conn, statement, params=None):
        try:
            return {"available": True, "rows": conn.execute(statement, params).fetchall()}
        except Exception:
            # autocommit keeps a failed optional query from poisoning the next one.
            return {"available": False, "rows": []}

    def collect(self):
        if not self.settings.postgres_dsn:
            return []
        report = {
            "scope": "cluster",
            "database": "cluster",
            "available": False,
            "pg_stat_statements": {"available": False, "rows": []},
        }
        try:
            with self.connect() as conn:
                extension = conn.execute("""
                    SELECT n.nspname,
                           EXISTS (
                               SELECT 1 FROM pg_class c
                               JOIN pg_attribute a ON a.attrelid = c.oid
                               WHERE c.relnamespace = n.oid
                                 AND c.relname = 'pg_stat_statements'
                                 AND a.attname = 'shared_blk_read_time'
                                 AND NOT a.attisdropped
                           ) AS split_io_times
                    FROM pg_extension e
                    JOIN pg_namespace n ON n.oid = e.extnamespace
                    WHERE e.extname = 'pg_stat_statements'
                """).fetchone()
                if not extension:
                    return [report]
                # PostgreSQL 17 renamed these columns. Keep the requested wire names.
                read_time = "shared_blk_read_time" if extension["split_io_times"] else "blk_read_time"
                write_time = "shared_blk_write_time" if extension["split_io_times"] else "blk_write_time"
                statement = sql.SQL("""
                    SELECT
                        current_timestamp AS collected_at,
                        version() AS postgres_version,
                        d.datname AS database,
                        s.dbid,
                        s.userid,
                        s.queryid,
                        s.toplevel,
                        s.query,
                        s.calls,
                        ROUND(s.total_exec_time::numeric, 2) AS total_exec_ms,
                        ROUND(s.mean_exec_time::numeric, 2) AS mean_exec_ms,
                        ROUND(s.min_exec_time::numeric, 2) AS min_exec_ms,
                        ROUND(s.max_exec_time::numeric, 2) AS max_exec_ms,
                        ROUND(s.stddev_exec_time::numeric, 2) AS stddev_exec_ms,
                        s.rows,
                        s.shared_blks_hit,
                        s.shared_blks_read,
                        s.shared_blks_dirtied,
                        s.shared_blks_written,
                        s.temp_blks_read,
                        s.temp_blks_written,
                        s.{read_time} AS blk_read_time,
                        s.{write_time} AS blk_write_time,
                        i.stats_reset
                    FROM {statements} s
                    JOIN pg_database d ON d.oid = s.dbid
                    CROSS JOIN {info} i
                    ORDER BY s.total_exec_time DESC
                    LIMIT 30
                """).format(
                    statements=sql.Identifier(extension["nspname"], "pg_stat_statements"),
                    info=sql.Identifier(extension["nspname"], "pg_stat_statements_info"),
                    read_time=sql.Identifier(read_time),
                    write_time=sql.Identifier(write_time),
                )
                section = self.query(conn, statement)
                # queryid is an int64; use a string so JSON/JavaScript preserves every digit.
                section["rows"] = [
                    {**row, "queryid": str(row["queryid"]) if row["queryid"] is not None else None}
                    for row in section["rows"]
                ]
                report["pg_stat_statements"] = section
                report["available"] = section["available"]
        except Exception:
            # Never log SQL text, DSNs or driver exceptions.
            pass
        return [report]
