from __future__ import annotations

import ast
from pathlib import Path
import tomllib


ROOT = Path(__file__).parents[3]


def test_api_extra_explicitly_declares_every_direct_framework_dependency() -> None:
    """Break caught: a direct API import works only through a transitive dependency."""

    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = metadata["project"]["optional-dependencies"]

    assert extras["api"] == [
        "argon2-cffi>=23.1,<26",
        "email-validator>=2.2,<3",
        "fastapi>=0.115,<1",
        "PyJWT>=2.9,<3",
        "pydantic>=2,<3",
        "uvicorn>=0.34,<1",
    ]
    assert set(extras["api"]) <= set(extras["dev"])

    imported_roots: set[str] = set()
    for path in (ROOT / "src" / "fb_crawl" / "api").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.partition(".")[0])

    assert {"fastapi", "pydantic"} <= imported_roots
