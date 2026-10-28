"""安装入口的数据保护与失败处理；不改当前项目环境、不联网。"""
from contextlib import redirect_stdout
import importlib.util
from io import StringIO
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/install_environment.py"
spec = importlib.util.spec_from_file_location("qfund_installer", SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@unittest.skipUnless(os.name == "nt", "Windows源码安装入口")
class InstallEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="QFund 安装测试 ")
        self.addCleanup(self.temp.cleanup)
        # 与安装入口一致，展开Windows临时目录中的8.3短名。
        self.root = Path(self.temp.name).resolve()
        for name in ("pyproject.toml", "requirements.lock.txt", "src/fundlab/__init__.py", "启动工作台.cmd"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test", encoding="utf-8")

    def test_incomplete_source_does_not_create_environment(self):
        (self.root / 'requirements.lock.txt').unlink()
        with self.assertRaises(installer.InstallError):
            installer.install(self.root)
        self.assertFalse((self.root / '.venv').exists())

    def test_unsupported_python_rejected_before_writes(self):
        with patch.object(installer.sys, 'version_info', (3, 8)), self.assertRaises(installer.InstallError):
            installer.install(self.root)
        self.assertFalse((self.root / '.local').exists())

    def test_incomplete_environment_and_user_data_are_preserved(self):
        sentinel = self.root / '.venv/keep.txt'
        sentinel.parent.mkdir()
        sentinel.write_bytes(b'old environment')
        data = self.root / 'data/fundlab.sqlite3'
        data.parent.mkdir()
        data.write_bytes(b'user database')
        with self.assertRaises(installer.InstallError):
            installer.install(self.root)
        self.assertEqual(sentinel.read_bytes(), b'old environment')
        self.assertEqual(data.read_bytes(), b'user database')

    def test_existing_environment_is_checked_without_pip_install(self):
        python = self.root / '.venv/Scripts/python.exe'
        python.parent.mkdir(parents=True)
        python.write_bytes(b'sentinel')
        with patch.object(installer, 'verify') as verify, patch.object(installer, 'run') as run:
            installer.install(self.root)
        verify.assert_called_once_with(self.root, python)
        run.assert_not_called()

    def test_failed_existing_check_never_repairs_or_overwrites(self):
        python = self.root / '.venv/Scripts/python.exe'
        python.parent.mkdir(parents=True)
        python.write_bytes(b'sentinel')
        with patch.object(installer, 'verify', side_effect=installer.InstallError('broken')), patch.object(installer, 'run') as run:
            with self.assertRaises(installer.InstallError):
                installer.install(self.root)
        run.assert_not_called()
        self.assertEqual(python.read_bytes(), b'sentinel')

    def test_invalid_wheelhouse_does_not_create_environment(self):
        with self.assertRaises(installer.InstallError):
            installer.install(self.root, self.root / 'missing')
        self.assertFalse((self.root / '.venv').exists())

    def test_failed_dependency_install_stops_before_success(self):
        out = StringIO()
        with patch.object(installer, 'run', side_effect=[None, installer.InstallError('offline')]), patch.object(installer, 'verify') as verify, redirect_stdout(out):
            with self.assertRaises(installer.InstallError):
                installer.install(self.root)
        verify.assert_not_called()
        self.assertNotIn('安装检查完成', out.getvalue())

    def test_parallel_install_lock_refuses_second_writer(self):
        with installer.installation_lock(self.root):
            with self.assertRaises(installer.InstallError):
                with installer.installation_lock(self.root):
                    self.fail('second writer acquired lock')

    def test_cmd_has_crlf_and_preserves_paths_with_spaces(self):
        source = SCRIPT.parents[1] / '安装环境.cmd'
        raw = source.read_bytes()
        self.assertNotIn(b'\n', raw.replace(b'\r\n', b''))
        target = self.root / source.name
        target.write_bytes(raw)
        env = dict(os.environ, QFUND_SETUP_NO_PAUSE='1')
        outcome = subprocess.run([os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', str(target)],
                                 cwd=self.root, env=env, capture_output=True, encoding='utf-8', timeout=10)
        self.assertEqual(outcome.returncode, 1)
        self.assertIn('项目文件不完整', outcome.stdout)


if __name__ == '__main__':
    unittest.main()
