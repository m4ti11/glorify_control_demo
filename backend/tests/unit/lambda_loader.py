import importlib.util
from pathlib import Path
import sys


def load_lambda(name, components=None):
    source = Path(__file__).resolve().parents[2] / "lambda" / name
    modules = {}
    names = components or ((["notification"] if (source / "notification.py").exists() else []) + ["models", "repository", "service", "handler"])
    if name in ("generate_report", "get_operational_report"):
        names = ["report_pdf", *names]
    previous = {component: sys.modules.get(component) for component in names}
    try:
        for component in names:
            component_path = source / f"{component}.py"
            if component == "report_pdf":
                component_path = source.parents[1] / "shared/report_pdf.py"
            spec = importlib.util.spec_from_file_location(component, component_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[component] = module
            spec.loader.exec_module(module)
            modules[component] = module
    finally:
        # Restore only Lambda-local imports. Removing third-party package modules
        # leaves cached ReportLab classes referring to different module instances.
        for component, original in previous.items():
            if original is None:
                sys.modules.pop(component, None)
            else:
                sys.modules[component] = original
    return modules
