from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import logging

from models import MetricsPayload
from repository import MetricsRepository

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class MetricsService:
    def __init__(self, repository: MetricsRepository) -> None:
        self.repository = repository

    @staticmethod
    def _utc_timestamp(value: datetime) -> str:
        return (
            value.astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _resources(metrics: MetricsPayload) -> list[dict[str, Any]]:
        resources = [
            {
                "resource_type": "HOST",
                "resource_id": "host",
                "metrics": metrics.host.model_dump(mode="json"),
            }
        ]
        resources.extend(
            {
                "resource_type": "CONTAINER",
                "resource_id": container.name,
                "metrics": container.model_dump(mode="json"),
            }
            for container in metrics.docker.containers
        )
        if metrics.postgres is not None:
            resources.append(
                {
                    "resource_type": "POSTGRES",
                    "resource_id": "postgres",
                    "metrics": metrics.postgres.model_dump(mode="json"),
                }
            )
        return resources

    def ingest(
        self, metrics: MetricsPayload, request_id=None
    ) -> tuple[str, str, int, bool]:
        # Lambda's clock is AWS-managed; do not trust the agent timestamp as receipt time.
        received_at = self._utc_timestamp(datetime.now(timezone.utc))
        measured_at = self._utc_timestamp(metrics.timestamp)
        resources = self._resources(metrics)
        created = self.repository.save_resource_metrics(
            metrics.system_id, measured_at, received_at, resources
        )
        logger.info(
            "metrics.persisted system_id=%s measured_at=%s created=%s resource_count=%s cpu_percent=%s api_request_id=%s",
            metrics.system_id,
            measured_at,
            created,
            len(resources),
            metrics.host.cpu_percent,
            request_id,
        )
        return measured_at, received_at, len(resources), created
