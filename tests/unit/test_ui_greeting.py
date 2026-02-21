from pathlib import Path


def test_index_template_includes_customer_greeting() -> None:
    template = Path("orchestrator/app/templates/index.html").read_text(encoding="utf-8")

    assert "Welcome!" in template
    assert "We’re glad you’re here." in template
