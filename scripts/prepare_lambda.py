#!/usr/bin/env python3
"""Package backend Lambdas for Terraform (Python 3.12, x86_64)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
FUNCTIONS = ("ingest_metrics", "get_latest_metrics", "vps_authorizer", "process_events", "get_events",
             "ingest_pg_statements", "get_pg_statements", "configuration", "sync_notification_channels", "evaluate_host_alerts", "dashboard_token_scopes",
             "generate_report", "get_operational_report", "health_check")


def package_lambda(name: str) -> None:
    source = ROOT / "backend/lambda" / name
    lock = source / "requirements.lock"
    if not lock.is_file():
        raise FileNotFoundError(f"Missing dependency lock: {lock}")
    package = ROOT / "build" / f"{name}.zip"
    package.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lambda-", dir=package.parent) as temporary:
        staging = Path(temporary) / "package"
        staging.mkdir()
        # Download target-runtime wheels even when run on macOS, Windows or ARM.
        # Lambda's default architecture in this Terraform module is x86_64.
        if lock.is_file():
            subprocess.run(
                [
                    sys.executable, "-m", "pip", "install",
                    "--disable-pip-version-check", "--no-compile", "--require-hashes",
                    "--platform", "manylinux2014_x86_64",
                    "--implementation", "cp", "--python-version", "3.12",
                    "--abi", "cp312", "--only-binary=:all:",
                    "--target", str(staging), "-r", str(lock),
                ],
                check=True,
            )
        # Keep the source tree structure; handler.py must be at the ZIP root.
        shutil.copytree(
            source, staging, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "*.pyo", ".venv", ".git", ".env", ".env.*", "test", "tests",
            ),
        )
        if name in ("generate_report", "get_operational_report"):
            shutil.copy2(ROOT / "backend/shared/report_pdf.py", staging / "report_pdf.py")
        archive_path = Path(temporary) / package.name
        with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(staging).as_posix())
        archive_path.replace(package)
    print(f"ZIP: {package}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages-only", action="store_true", help="Compatibility flag; this script only builds packages")
    parser.add_argument("--function", action="append", choices=FUNCTIONS)
    args = parser.parse_args()
    for name in dict.fromkeys(args.function or FUNCTIONS):
        package_lambda(name)


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError) as error:
        sys.exit(f"Build failed: {error}")
