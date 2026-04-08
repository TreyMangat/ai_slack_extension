from pathlib import Path


def test_web_app_includes_new_button_demo() -> None:
    app_path = Path("web/src/App.jsx")
    content = app_path.read_text(encoding="utf-8")

    assert "Click me" in content
    assert "Button clicks:" in content
    assert "useState" in content
