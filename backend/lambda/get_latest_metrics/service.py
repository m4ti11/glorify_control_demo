from __future__ import annotations

from models import LatestMetricsSnapshot
from repository import MetricsRepository


class MetricsService:
    def __init__(self, repository: MetricsRepository) -> None:
        self.repository = repository

    def get_latest(self) -> LatestMetricsSnapshot | None:
        snapshot = self.repository.get_latest_snapshot()
        if snapshot is None:
            return None
        return LatestMetricsSnapshot.model_validate(snapshot)
