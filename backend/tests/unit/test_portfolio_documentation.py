"""Keep the portfolio entry point and its local navigation usable."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]


def test_portfolio_entrypoint_and_documentation_links():
    readme = ROOT / "README.md"
    text = readme.read_text()
    assert "## Architecture" in text
    assert "docs/images/Glorify_control.drawio.png" in text
    assert "## Design scope" in text
    for document in [readme, *sorted((ROOT / "docs").glob("*.md"))]:
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", document.read_text()):
            if target.startswith(("https://", "http://", "mailto:", "#")):
                continue
            path = target.split("#")[0]
            assert (document.parent / path).exists(), f"{document.relative_to(ROOT)}: {path}"
