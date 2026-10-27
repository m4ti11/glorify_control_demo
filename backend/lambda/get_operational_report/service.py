from __future__ import annotations


class OperationalReportService:
    def __init__(self, repository):
        self.repository = repository

    def get(self, query):
        report = self.repository.get(query.key)
        if report is None:
            return None
        if report.get("system_id") != query.system_id or report.get("report_date") != query.report_date:
            raise RuntimeError("Stored operational report identity mismatch")
        return report
