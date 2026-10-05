"""Deployment guards for the Vercel entrypoint and `vercel.json`.

Vercel statically scans every ``api/*.py`` file *before* it builds and only
turns a file into a Serverless Function when it finds a top-level ``app``,
``application``, or ``handler`` name. Import aliases such as
``from app import Handler as handler`` are invisible to that scan, so the file
is skipped and the deployment dies with:

    Error: The pattern "api/index.py" defined in `functions` doesn't match any
    Serverless Functions inside the `api` directory.

These tests pin the contract on both sides: the entrypoint must keep a real
top-level ``handler`` class, and every ``functions`` key must keep pointing at
a file that exists.
"""
from __future__ import annotations

import ast
import fnmatch
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = ROOT / "api" / "index.py"
VERCEL_JSON = ROOT / "vercel.json"
API_DIR = ROOT / "api"

# Names the Vercel Python runtime accepts as an entrypoint variable.
ENTRYPOINT_NAMES = {"app", "application", "handler"}


class VercelEntrypointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tree = ast.parse(ENTRYPOINT.read_text(encoding="utf-8"))

    def test_entrypoint_declares_a_top_level_handler_class(self):
        handler_classes = [
            node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef) and node.name.lower() == "handler"
        ]
        self.assertEqual(
            len(handler_classes),
            1,
            "api/index.py must declare exactly one top-level class named "
            "`handler`; Vercel's scanner ignores import aliases such as "
            "`from app import Handler as handler`.",
        )
        self.assertTrue(
            handler_classes[0].bases,
            "The `handler` class must inherit the application's "
            "BaseHTTPRequestHandler subclass.",
        )

    def test_entrypoint_variable_is_not_only_an_import_alias(self):
        aliased = {
            alias.asname or alias.name
            for node in self.tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        defined = {
            node.name
            for node in self.tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        }
        defined |= {
            target.id
            for node in self.tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        detected = {name.lower() for name in defined if name.lower() in ENTRYPOINT_NAMES}
        self.assertTrue(
            detected,
            "api/index.py must define a top-level `app`, `application`, or "
            f"`handler`; import aliases {sorted(aliased)} do not count.",
        )

    def test_runtime_handler_contract(self):
        """`api.index:handler` must load the way Vercel's runtime loads it."""
        script = (
            "import importlib;"
            "from http.server import BaseHTTPRequestHandler;"
            "m = importlib.import_module('api.index');"
            "obj = getattr(m, 'handler');"
            "assert isinstance(obj, type), 'handler must be a class';"
            "assert issubclass(obj, BaseHTTPRequestHandler), 'handler must "
            "inherit BaseHTTPRequestHandler';"
            "assert all(hasattr(obj, name) for name in ('do_GET', 'do_POST'));"
            "print('ok')"
        )
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                **os.environ,
                "DATA_DIR": data_dir,
                "APP_ENV": "development",
                "ADMIN_TOKEN": "entrypoint-contract-test",
                "PYTHONPATH": str(ROOT),
            }
            env.pop("VERCEL", None)
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)


class VercelConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = json.loads(VERCEL_JSON.read_text(encoding="utf-8"))

    def test_every_functions_key_matches_a_real_api_file(self):
        functions = self.config.get("functions", {})
        self.assertTrue(functions, "vercel.json should configure the function")
        api_files = [
            path.relative_to(ROOT).as_posix() for path in API_DIR.rglob("*.py")
        ]
        for pattern in functions:
            matches = [
                name
                for name in api_files
                if name == pattern or fnmatch.fnmatch(name, pattern)
            ]
            self.assertTrue(
                matches,
                f"The `functions` pattern {pattern!r} matches no file in api/ "
                f"(found {api_files}); Vercel rejects the deployment with "
                "`unmatched-function-pattern`.",
            )

    def test_file_globs_are_single_strings(self):
        for pattern, settings in self.config.get("functions", {}).items():
            for key in ("includeFiles", "excludeFiles"):
                if key in settings:
                    self.assertIsInstance(
                        settings[key],
                        str,
                        f"functions[{pattern!r}].{key} must be a single glob "
                        "string, not a list.",
                    )

    def test_rewrite_destination_points_at_the_entrypoint(self):
        destinations = {
            rewrite.get("destination") for rewrite in self.config.get("rewrites", [])
        }
        self.assertIn("/api/index", destinations)
        self.assertTrue((API_DIR / "index.py").is_file())


if __name__ == "__main__":
    unittest.main()
