#!/usr/bin/env python3
"""Validate isolated IaC copies without reading local tfvars, secrets or state."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def prepare(destination):
    for module in ("bootstrap", "environments/prod"):
        source = ROOT / "infrastructure" / module
        target = destination / "infrastructure" / module
        target.mkdir(parents=True)
        for path in [*source.glob("*.tf"), source / ".terraform.lock.hcl"]:
            shutil.copy2(path, target / path.name)
        if (source / "branding").is_dir():
            shutil.copytree(source / "branding", target / "branding")
    build = destination / "build"; build.mkdir()
    # These archives are validation fixtures only, never deployment packages.
    for source in (ROOT / "backend/lambda").iterdir():
        if source.is_dir() and (source / "handler.py").is_file():
            with ZipFile(build / (source.name + ".zip"), "w") as archive:
                archive.writestr("handler.py", "# Terraform validation fixture; do not deploy")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-dir", type=Path, help="Optional installed provider directory, for offline validation")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="glorify-iac-check-") as temporary:
        destination = Path(temporary)
        prepare(destination)
        subprocess.run(["terraform", "fmt", "-check", "-recursive", str(destination / "infrastructure")], check=True)
        for module in ("bootstrap", "environments/prod"):
            target = destination / "infrastructure" / module
            command = ["terraform", "init", "-backend=false", "-input=false", "-lockfile=readonly"]
            if args.plugin_dir:
                command.append("-plugin-dir=" + str(args.plugin_dir.resolve()))
            subprocess.run(command, cwd=target, check=True)
            subprocess.run(["terraform", "validate", "-no-color"], cwd=target, check=True)


if __name__ == "__main__":
    main()
