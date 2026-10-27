"""Build contracts verified without installing packages or calling cloud APIs."""
from pathlib import Path
import runpy
from unittest.mock import patch
import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_packager_uses_hashed_lockfile_for_every_lambda(tmp_path):
    script = runpy.run_path(str(ROOT / "scripts/prepare_lambda.py"))
    package = script["package_lambda"]
    package.__globals__["ROOT"] = tmp_path
    for name in script["FUNCTIONS"]:
        source = tmp_path / "backend/lambda" / name
        source.mkdir(parents=True)
        (source / "handler.py").write_text("def lambda_handler(event, context): return {}")
        (source / "requirements.txt").write_text("example>=1")
        (source / "requirements.lock").write_text("# deliberately empty test lock")
    shared = tmp_path / "backend/shared"; shared.mkdir()
    (shared / "report_pdf.py").write_text("# shared")
    with patch.object(package.__globals__["subprocess"], "run") as install:
        for name in script["FUNCTIONS"]:
            package(name)
            command = install.call_args.args[0]
            assert "--require-hashes" in command
            assert command[-1] == str(tmp_path / "backend/lambda" / name / "requirements.lock")


def test_missing_lock_fails_before_installing_anything(tmp_path):
    package = runpy.run_path(str(ROOT / "scripts/prepare_lambda.py"))["package_lambda"]
    package.__globals__["ROOT"] = tmp_path
    source = tmp_path / "backend/lambda/ingest_metrics"; source.mkdir(parents=True)
    (source / "requirements.txt").write_text("boto3>=1")
    with patch.object(package.__globals__["subprocess"], "run") as install:
        with pytest.raises(FileNotFoundError, match="lock"):
            package("ingest_metrics")
        install.assert_not_called()


def test_all_deployable_components_have_locks_and_ci_runs_required_gates():
    script = runpy.run_path(str(ROOT / "scripts/prepare_lambda.py"))
    for name in script["FUNCTIONS"]:
        assert (ROOT / "backend/lambda" / name / "requirements.lock").is_file(), name
    for relative in ("agent/requirements.lock", "agent/requirements-dev.lock", "backend/tests/requirements.lock"):
        assert (ROOT / relative).is_file()
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    for required in ("push:", "pull_request:", "pytest agent/tests", "pytest backend/tests/unit", "npm --prefix frontend test", "fmt -check -recursive", "-backend=false", "terraform validate", "hashFiles", "cache"):
        assert required in ci


def test_agent_installer_pins_downloads_before_stopping_service():
    script = (ROOT / "agent/deploy/install.sh").read_text()
    assert "requirements.lock" in script and "requirements-build.lock" in script
    assert "pip download --require-hashes" in script
    assert "--no-build-isolation" in script
    assert script.index("pip download --require-hashes") < script.index("systemctl stop")


def test_lambda_sdk_dependencies_are_pinned_instead_of_inherited_from_runtime():
    for source in (ROOT / "backend/lambda").iterdir():
        if not source.is_dir():
            continue
        if any("import boto3" in p.read_text() for p in source.glob("*.py")):
            assert "boto3==" in (source / "requirements.lock").read_text(), source.name
