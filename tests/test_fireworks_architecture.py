from __future__ import annotations

from pathlib import Path


def test_fireworks_dataplane_does_not_import_openai_products() -> None:
    root = Path(__file__).resolve().parents[1]
    for path in (root / "app" / "dataplane" / "fireworks").glob("*.py"):
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "app.products.openai" not in text, f"{path} imports app.products.openai"
