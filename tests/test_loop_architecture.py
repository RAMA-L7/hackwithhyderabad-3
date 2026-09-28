"""Structure guards: layer import rules, no Rama/Hindsight imports, session id on every log line."""

from __future__ import annotations

import ast
import io
import unittest
from pathlib import Path

import loop_support  # noqa: F401  (path wiring)
from loop_support import RELEVANT_VIEW, RESOLVED, FakeLLM, FakeMemoryPort, ScriptedEngineer, hyp
from debugagent.app import build_investigation
from debugagent.domain.models import DebugInput
from debugagent.logging_setup import configure_logging

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "debugagent"
# layer -> layers it may import (besides itself). cli/app are the composition root and may import anything.
ALLOWED = {
    "domain": set(),
    "ports": {"domain"},
    "views": {"domain"},
    "services": {"domain", "ports", "logging_setup"},
    "adapters": {"domain", "ports", "logging_setup"},
    "controllers": {"domain", "ports", "services", "views", "adapters"},
}
RAMA_MODULES = {"schemas", "config", "memory", "seeds"}  # rama-m0 owns these; this branch never imports them


def debugagent_imports(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
            [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
        for name in names:
            parts = name.split(".")
            if parts[0] == "debugagent" and len(parts) > 1:
                found.add(parts[1])
            elif parts[0] == "hindsight_client":
                found.add("hindsight_client")
    return found


class LayerTests(unittest.TestCase):
    def test_layers_only_import_what_they_may(self):
        for layer, allowed in ALLOWED.items():
            for module in (PACKAGE / layer).rglob("*.py"):
                with self.subTest(module=str(module.relative_to(PACKAGE))):
                    illegal = debugagent_imports(module) - allowed - {layer}
                    self.assertEqual(illegal, set(), f"{module.name} imports {illegal}")

    def test_no_rama_or_hindsight_imports(self):
        for module in PACKAGE.rglob("*.py"):
            with self.subTest(module=str(module.relative_to(PACKAGE))):
                self.assertEqual(debugagent_imports(module) & (RAMA_MODULES | {"hindsight_client"}), set())

    def test_no_package_init_at_debugagent_root(self):  # namespace package: merges cleanly with rama-m0
        self.assertFalse((PACKAGE / "__init__.py").exists())


class LoggingTests(unittest.TestCase):
    def test_every_line_carries_the_session_id(self):
        stream = io.StringIO()
        configure_logging(stream=stream)
        raw = DebugInput("photo-api resets uploads over 2 MB\nservice=photo-api")
        llm = FakeLLM({"hypotheses": [hyp(cites=["3f9a1c07b2e4d815"]), hyp()]})
        build_investigation(FakeMemoryPort(RELEVANT_VIEW), llm).run(raw, ScriptedEngineer(resolution=RESOLVED),
                                                                   session_id="sess42")
        lines = stream.getvalue().strip().splitlines()
        self.assertGreaterEqual(len(lines), 4)
        self.assertTrue(all("session=sess42" in line for line in lines), lines)
        self.assertTrue(any("recall bank=fake-bank" in line for line in lines))
        self.assertTrue(any("retention retained=True" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
