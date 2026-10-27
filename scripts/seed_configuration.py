#!/usr/bin/env python3
"""Create missing base alert rules in AWS without overwriting edited settings."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
from uuid import UUID

import boto3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/lambda/configuration"))
from repository import ConfigurationRepository  # noqa: E402
from service import ConfigurationService  # noqa: E402


def seed(repository, system_id, actor):
    service = ConfigurationService(repository, None)
    channels = [c["id"] for c in repository.all("notification-channels") if c["data"]["enabled"]]
    existing = repository.all("alert-rules")
    for metric, name, threshold, recovery in (
        ("cpu_percent", "CPU", 80, 70), ("memory_percent", "Memory", 85, 75), ("disk_percent", "Disk", 90, 80)
    ):
        matching = next((r for r in existing if r["data"]["system_id"] == system_id and
                         r["data"]["metric"] == metric and r["data"]["resource_type"] == "host" and
                         r["data"]["resource_id"] == "host"), None)
        digest = hashlib.sha256(f"glorify-control:base:{system_id}:{metric}".encode()).digest()[:16]
        uid = str(UUID(bytes=digest, version=5))
        if matching or repository.get("alert-rules", uid):
            print(f"Conservada: {name}")
            continue
        service.save("alert-rules", uid, {"name": name, "system_id": system_id, "resource_type": "host",
            "resource_id": "host", "metric": metric, "threshold": threshold, "recovery_threshold": recovery,
            "notification_channel_ids": channels}, actor)
        print(f"Created: {name} — alert {threshold}%, recovery {recovery}%")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="glorify-control-prod")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--table", default="glorify-control-prod-configuration")
    parser.add_argument("--metrics-table", default="glorify-control-prod-metrics")
    parser.add_argument("--system-id", help="By default use the latest agent snapshot")
    args = parser.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    client = session.client("dynamodb")
    system_id = args.system_id
    if not system_id:
        snapshot = client.get_item(TableName=args.metrics_table,
            Key={"PK": {"S": "METRICS"}, "SK": {"S": "SNAPSHOT#LATEST"}},
            ProjectionExpression="system_id", ConsistentRead=True)
        system_id = snapshot.get("Item", {}).get("system_id", {}).get("S")
    if not system_id:
        parser.error("No agent snapshot exists. Pass the VPS GLORIFY_SYSTEM_ID using --system-id.")
    # Keep the same audit actor format for all rules created by this operation.
    identity = session.client("sts").get_caller_identity()["Arn"]
    seed(ConfigurationRepository(args.table, client), system_id, identity)


if __name__ == "__main__":
    main()
