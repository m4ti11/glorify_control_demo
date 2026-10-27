from pathlib import Path
import re
ROOT = Path(__file__).resolve().parents[3]

def test_frontend_data_boundary_has_no_cloud_adapter_or_login():
    frontend = ROOT / "frontend"
    for name in ("config.js", "config.example.js", "auth.js", "login.js"):
        assert not (frontend / name).exists()
    for source in frontend.glob("*.js"):
        text = source.read_text()
        assert "Authorization" not in text and "getAccessToken" not in text
        assert "cognito" not in "\n".join(line for line in text.lower().splitlines() if not line.startswith("//"))
        if source.name != "api.js":
            assert not re.search(r"\bfetch\s*\(", text), source.name
    api = (frontend / "api.js").read_text()
    assert "new URL('./demo/' + FILES[kind], import.meta.url)" in api
