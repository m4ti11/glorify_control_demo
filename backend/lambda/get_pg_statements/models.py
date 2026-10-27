from __future__ import annotations

from datetime import date
import re

MAX_REPORT_BYTES = 1024 * 1024


class ReportQuery:
    def __init__(self, params):
        params = params or {}
        if set(params) != {"system_id", "report_date"}:
            raise ValueError("system_id and report_date are required")
        self.system_id = params["system_id"]
        raw_date = params["report_date"]
        if not isinstance(self.system_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.system_id):
            raise ValueError("Invalid system_id")
        if not isinstance(raw_date, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw_date):
            raise ValueError("report_date must use YYYY-MM-DD")
        self.report_date = date.fromisoformat(raw_date)
        self.key = f"raw/pg-statements/{self.system_id}/{self.report_date:%Y/%m/%d}.json.gz"
