"""Verify silent exception handlers were replaced with logging."""
import ast
import pathlib


CHECKED_FILES = [
    "orchestrator/app/slackbot.py",
    "orchestrator/app/services/coderunner_adapter.py",
    "orchestrator/app/tasks/jobs.py",
]


def test_no_bare_pass_in_except_handlers():
    """No except block should contain only 'pass'."""
    for filepath in CHECKED_FILES:
        path = pathlib.Path(filepath)
        if not path.exists():
            continue
        source = path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                body = node.body
                if len(body) == 1 and isinstance(body[0], ast.Pass):
                    assert False, (
                        f"{filepath}:{node.lineno} has bare 'pass' "
                        f"in except handler - should use logger.exception()"
                    )


def test_no_diagnostic_print_statements():
    """No diagnostic print statements should remain in production code."""
    for filepath in pathlib.Path("orchestrator/app").rglob("*.py"):
        source = filepath.read_text()
        assert "PRFACTORY DIAG" not in source, (
            f"{filepath} still has diagnostic print statements"
        )
