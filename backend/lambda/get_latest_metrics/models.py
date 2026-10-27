from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class ResourceMetrics(BaseModel):
    resource_type: Literal["HOST", "CONTAINER", "POSTGRES"]
    resource_id: str
    metrics: dict[str, Any]


class LatestMetricsSnapshot(BaseModel):
    system_id: str
    measured_at: str
    received_at: str
    resources: list[ResourceMetrics]
