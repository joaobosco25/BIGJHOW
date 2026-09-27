from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_compose_keeps_database_private_and_hardens_web_container():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    db_block, web_block = compose.split("  web:", 1)
    assert "ports:" not in db_block
    assert '"127.0.0.1:8000:8000"' in web_block
    assert "read_only: true" in web_block
    assert "no-new-privileges:true" in web_block
    assert "cap_drop:\n      - ALL" in web_block
    assert "/tmp:size=64m,mode=1777" in web_block
    assert "backend:\n    internal: true" in compose
    assert "/var/run/docker.sock" not in compose
    assert "privileged:" not in compose


def test_runtime_image_uses_unprivileged_user():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER bigjhow" in dockerfile
    assert "PYTHONDONTWRITEBYTECODE=1" in dockerfile
