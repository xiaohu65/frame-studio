"""Interpreter selection in an isolated copy; never starts the real launcher."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMMAND = ROOT / "启动帧工坊.command"


@unittest.skipUnless(shutil.which("zsh"), "the macOS command requires zsh")
class LauncherPythonTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="frame-python-test-")
        self.addCleanup(temporary.cleanup)
        self.root = (Path(temporary.name) / "project with spaces").resolve()
        self.root.mkdir()
        self.command = self.root / COMMAND.name
        shutil.copyfile(COMMAND, self.command)
        self.bin = Path(temporary.name) / "path bin"
        self.bin.mkdir()
        self.receipt = Path(temporary.name) / "selected.json"
        self.env = dict(os.environ)
        for key in ("FRAME_STUDIO_PYTHON", "VIRTUAL_ENV", "PYTHONHOME", "ZDOTDIR"):
            self.env.pop(key, None)
        self.env.update(PATH=str(self.bin), FRAME_TEST_RECEIPT=str(self.receipt))
        self.zsh = shutil.which("zsh")

    def fake_python(self, path, name, probe_status=0, exit_status=0):
        path.parent.mkdir(parents=True, exist_ok=True)
        # /bin/sh is independent of the deliberately minimal PATH.
        path.write_text(
            "#!/bin/sh\n"
            f"if [ \"$1\" = '-c' ]; then exit {probe_status}; fi\n"
            f"exec {shlex_quote(shutil.which('python3'))} -c "
            + shlex_quote("import json,os,sys; from pathlib import Path; "
                          "Path(os.environ['FRAME_TEST_RECEIPT']).write_text(json.dumps("
                          "{'name':sys.argv[1],'cwd':os.getcwd(),'args':sys.argv[2:]}))")
            + f" {shlex_quote(name)} \"$@\"\n"
        )
        if exit_status:
            # Record first, then preserve the final interpreter's exit status.
            text = path.read_text().replace("exec ", "", 1) + f"exit {exit_status}\n"
            path.write_text(text)
        path.chmod(0o755)
        return path

    def run_command(self, *args, **env):
        return subprocess.run([self.zsh, str(self.command), *args],
                              cwd=self.root.parent, env={**self.env, **env},
                              text=True, capture_output=True, timeout=5)

    def read_receipt(self):
        return json.loads(self.receipt.read_text())

    def test_explicit_path_with_spaces_wins_and_forwards_arguments_from_project(self):
        explicit = self.fake_python(self.root.parent / "custom env/bin/python", "explicit")
        self.fake_python(self.root / ".venv/bin/python", "project")
        activated = self.fake_python(self.root.parent / "active/bin/python", "active")
        self.fake_python(self.bin / "python3", "path")
        result = self.run_command("--json", "--port", "9234", "argument with spaces",
                                  FRAME_STUDIO_PYTHON=str(explicit), VIRTUAL_ENV=str(activated.parent.parent))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_receipt(), {
            "name": "explicit", "cwd": str(self.root),
            "args": ["scripts/frame_studio_launcher.py", "--json", "--port", "9234", "argument with spaces"],
        })

    def test_explicit_path_command_is_resolved_from_path(self):
        self.fake_python(self.bin / "custom-python", "named")
        self.fake_python(self.root / ".venv/bin/python", "project")
        result = self.run_command("--status", FRAME_STUDIO_PYTHON="custom-python")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_receipt()["name"], "named")

    def test_missing_explicit_override_does_not_fall_back(self):
        self.fake_python(self.root / ".venv/bin/python", "project")
        self.fake_python(self.bin / "python3", "path")
        for missing in (str(self.root / "missing/bin/python"), "missing-python"):
            with self.subTest(missing=missing):
                result = self.run_command(FRAME_STUDIO_PYTHON=missing)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("README「首次使用」", result.stderr)
                self.assertFalse(self.receipt.exists())

    def test_project_environment_wins_over_activated_environment(self):
        self.fake_python(self.root / ".venv/bin/python", "project")
        activated = self.fake_python(self.root.parent / "active/bin/python", "active")
        self.fake_python(self.bin / "python3", "path")
        result = self.run_command(VIRTUAL_ENV=str(activated.parent.parent))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_receipt()["name"], "project")

    def test_activated_environment_precedes_path(self):
        activated = self.fake_python(self.root.parent / "active/bin/python", "active")
        self.fake_python(self.bin / "python3", "path")
        result = self.run_command(VIRTUAL_ENV=str(activated.parent.parent))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_receipt()["name"], "active")

    def test_path_python3_is_used_without_a_virtual_environment(self):
        self.fake_python(self.bin / "python3", "path")
        result = self.run_command("--status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_receipt()["name"], "path")

    def test_old_python_does_not_fall_back_to_path(self):
        self.fake_python(self.bin / "python3", "path")
        old = self.fake_python(self.root.parent / "old/bin/python", "old", probe_status=1)
        result = self.run_command(FRAME_STUDIO_PYTHON=str(old))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("版本低于 3.12", result.stderr)
        self.assertFalse(self.receipt.exists())

    def test_missing_path_python_does_not_start_launcher(self):
        result = self.run_command()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("未找到 Python 命令", result.stderr)
        self.assertFalse(self.receipt.exists())

    def test_final_interpreter_exit_status_is_preserved(self):
        failing = self.fake_python(self.bin / "python3", "failing", exit_status=7)
        result = self.run_command("--status")
        self.assertEqual(result.returncode, 7)
        self.assertEqual(self.read_receipt()["name"], "failing")


def shlex_quote(value):
    import shlex
    return shlex.quote(value)


if __name__ == "__main__":
    unittest.main()
