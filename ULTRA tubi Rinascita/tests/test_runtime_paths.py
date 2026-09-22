import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import runtime_paths


class RuntimePathsTests(unittest.TestCase):
    def test_legacy_layout_falls_back_to_project_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "ULTRA tubi Rinascita"
            project.mkdir()
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("ULTRA_VARIABLES_DIR", None)
                resolved = runtime_paths.resolve_variables_root(str(project))
            self.assertTrue(os.path.samefile(resolved, project))

    def test_workspace_layout_finds_external_variables(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            project = workspace / "Program" / "ULTRA tubi Rinascita"
            variables = workspace / "Variables"
            project.mkdir(parents=True)
            variables.mkdir()
            (variables / "config.json").write_text("{}", encoding="utf-8")

            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("ULTRA_VARIABLES_DIR", None)
                resolved = runtime_paths.resolve_variables_root(str(project))

            self.assertTrue(os.path.samefile(resolved, variables))

    def test_environment_override_has_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "Program" / "ULTRA tubi Rinascita"
            override = Path(tmp) / "My Runtime Data"
            project.mkdir(parents=True)
            override.mkdir()

            with mock.patch.dict(
                os.environ,
                {"ULTRA_VARIABLES_DIR": str(override)},
                clear=False,
            ):
                resolved = runtime_paths.resolve_variables_root(str(project))

            self.assertTrue(os.path.samefile(resolved, override))


if __name__ == "__main__":
    unittest.main()
