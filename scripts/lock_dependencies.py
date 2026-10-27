#!/usr/bin/env python3
"""Regenerate Python 3.12/Linux x86_64 locks with reviewed uv 0.9.11."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    if subprocess.check_output(["uv", "--version"], text=True).strip() != "uv 0.9.11":
        raise SystemExit("Install uv==0.9.11 to regenerate reviewed locks")
    jobs = [
        (Path("agent/pyproject.toml"), Path("agent/requirements.lock"), []),
        (Path("agent/pyproject.toml"), Path("agent/requirements-dev.lock"), ["--extra", "dev"]),
        (Path("agent/requirements-build.in"), Path("agent/requirements-build.lock"), []),
        (Path("backend/tests/requirements.txt"), Path("backend/tests/requirements.lock"), []),
    ]
    for source in sorted((ROOT / "backend/lambda").glob("*/requirements.txt")):
        relative = source.relative_to(ROOT)
        jobs.append((relative, relative.with_name("requirements.lock"), []))
    for source, destination, extras in jobs:
        subprocess.run([
            "uv", "pip", "compile", str(source), "--python-version", "3.12",
            "--python-platform", "x86_64-manylinux_2_17", "--generate-hashes",
            "--no-annotate", "--no-header", "--only-binary", ":all:",
            "-q", "-o", str(destination), *extras,
        ], cwd=ROOT, check=True)
        output = ROOT / destination
        if not output.read_text().strip():
            output.write_text("# No third-party runtime dependencies.\n")
        print(f"LOCK {destination}", flush=True)


if __name__ == "__main__":
    main()
