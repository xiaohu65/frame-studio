"""The retired private seed must fail without touching project storage."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PrivateSeedEntryTest(unittest.TestCase):
    def run_seed(self, args, data):
        env = dict(os.environ, FRAME_STUDIO_DATA=str(data), PYTHONDONTWRITEBYTECODE='1')
        return subprocess.run([sys.executable, *args], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=5)

    def test_cli_explains_retirement_without_creating_data(self):
        with tempfile.TemporaryDirectory(prefix='frame-retired-seed-') as temporary:
            data = Path(temporary) / 'data'
            result = self.run_seed(['-m', 'backend.seed'], data)
            self.assertEqual(result.returncode, 2)
            self.assertIn('网页中新建项目', result.stderr)
            self.assertIn('导入自己的素材', result.stderr)
            self.assertEqual(result.stdout, '')
            self.assertNotIn('Traceback', result.stderr)
            self.assertFalse(data.exists())

    def test_import_and_function_do_not_load_store_or_touch_data(self):
        code = '''import sys
from backend.seed import MESSAGE, seed
assert 'backend.store' not in sys.modules
try:
    seed()
except RuntimeError as error:
    assert str(error) == MESSAGE
else:
    raise AssertionError('private seed must not run')
assert 'backend.store' not in sys.modules
'''
        with tempfile.TemporaryDirectory(prefix='frame-retired-seed-') as temporary:
            data = Path(temporary) / 'data'
            result = self.run_seed(['-c', code], data)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '')
            self.assertFalse(data.exists())


if __name__ == '__main__':
    unittest.main()
