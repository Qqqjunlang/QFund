"""源码与EXE数据隔离、原型任务保护；不读写真实数据或系统任务。"""
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
from io import StringIO
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.scheduling.windows_task import transport, WindowsTask, NS
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('qfund_frozen_entry', Path(__file__).resolve().parents[1] / 'scripts/frozen_entry.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
worker_spec = importlib.util.spec_from_file_location('qfund_frozen_worker_entry', Path(__file__).resolve().parents[1] / 'scripts/frozen_worker_entry.py')
worker_entry = importlib.util.module_from_spec(worker_spec)
worker_spec.loader.exec_module(worker_entry)


class FrozenRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='QFund EXE 测试 ')
        self.addCleanup(self.temp.cleanup)
        # Windows临时目录可能含8.3短名；入口会resolve，预期也使用规范路径。
        self.root = Path(self.temp.name).resolve()

    def test_source_root_remains_project_even_with_localappdata(self):
        with patch.object(sys, 'frozen', False, create=True), patch.dict(os.environ, LOCALAPPDATA=str(self.root)):
            self.assertEqual(AppConfig.default().root, Path(__file__).resolve().parents[1])

    def test_frozen_root_uses_user_directory_without_creating_it(self):
        with patch.object(sys, 'frozen', True, create=True), patch.dict(os.environ, LOCALAPPDATA=str(self.root)):
            config = AppConfig.default()
            self.assertEqual(config.root, self.root / 'QFund')
            self.assertEqual(config.database_path, self.root / 'QFund/data/fundlab.sqlite3')
            self.assertFalse(config.root.exists())

    def test_frozen_missing_or_relative_user_directory_does_not_fall_back(self):
        with patch.object(sys, 'frozen', True, create=True):
            for value in ('', 'relative'):
                with patch.dict(os.environ, LOCALAPPDATA=value), self.assertRaises(FundlabError) as caught:
                    AppConfig.default()
                self.assertEqual(caught.exception.code, 'DATA_DIRECTORY_UNAVAILABLE')

    def test_explicit_config_still_uses_supplied_root(self):
        with patch.object(sys, 'frozen', True, create=True):
            self.assertEqual(AppConfig(self.root).root, self.root)

    def test_frozen_task_uses_sibling_worker_and_explicit_data_root(self):
        exe = self.root / '程序 空格/QFund.exe'
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(exe)):
            task = WindowsTask(AppConfig(self.root / '用户 数据'))
        self.assertEqual(task.interpreter, exe.with_name('QFundWorker.exe'))
        self.assertEqual(task.arguments, subprocess.list2cmdline(['--project-root', str(task.root)]))
        self.assertNotIn('-m fundlab', task.arguments)
        xml = ET.fromstring(task.xml('18:00', 'S-1-test'))
        get = lambda name: xml.findtext('{'+NS+'}Actions/{'+NS+'}Exec/{'+NS+'}'+name)
        self.assertEqual(get('Command'), str(task.interpreter))
        self.assertEqual(get('Arguments'), task.arguments)
        self.assertEqual(get('WorkingDirectory'), str(task.root))

    def test_source_and_exe_cannot_silently_claim_same_action(self):
        config = AppConfig(self.root)
        source = WindowsTask(config)
        with patch.object(sys, 'frozen', True, create=True):
            frozen = WindowsTask(config)
        self.assertEqual(source.name, frozen.name)
        self.assertEqual(source.source, frozen.source)
        self.assertNotEqual(source.interpreter, frozen.interpreter)
        self.assertNotEqual(source.arguments, frozen.arguments)
        # PS1继续同时检查Source、Command、Arguments、WorkingDirectory，身份不同不会接管。

    def test_missing_frozen_worker_does_not_register_task(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(self.root/'QFund.exe')):
            task = WindowsTask(AppConfig(self.root))
        with patch.object(task, 'call') as call, self.assertRaises(FundlabError) as caught:
            task.register('18:00', {'installed':False, 'sid':'S-1-test'})
        self.assertEqual(caught.exception.code, 'WORKER_NOT_FOUND')
        call.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows任务传输')
    def test_frozen_transport_keeps_exact_identity_without_shell(self):
        request = {'operation':'query','name':'isolated','command':'QFundWorker.exe'}
        reply = subprocess.CompletedProcess([], 0, json.dumps({'ok':True,'installed':False}))
        with patch.object(sys, 'frozen', True, create=True), patch('fundlab.scheduling.windows_task.subprocess.run', return_value=reply) as run:
            self.assertFalse(transport(request)['installed'])
        self.assertEqual(json.loads(run.call_args.kwargs['input']), request)
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_frozen_worker_forwards_to_same_executor_and_preserves_exit_code(self):
        with patch.object(worker_entry, 'worker_main', return_value=2) as run:
            self.assertEqual(worker_entry.main(['--project-root', str(self.root)]), 2)
        run.assert_called_once_with(['--project-root', str(self.root.resolve())])

    def test_frozen_worker_rejects_relative_path_before_executor(self):
        with patch.object(worker_entry, 'worker_main') as run, redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            worker_entry.main(['--project-root', 'relative'])
        run.assert_not_called()

    def test_schedule_management_entry_does_not_launch_web_or_collect(self):
        with patch.object(entry, 'launch') as launch, patch('fundlab.scheduling.windows_task.WindowsTask.change', return_value={'installed':True}) as change, redirect_stdout(StringIO()):
            self.assertEqual(entry.main(['--data-root', str(self.root), '--schedule-action', 'install']), 0)
        change.assert_called_once_with('install')
        launch.assert_not_called()

    def test_diagnosis_cannot_also_modify_tasks(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            entry.main(['--diagnose','--schedule-action','install'])

    def test_entry_override_is_explicit_and_does_not_create_or_migrate_data(self):
        target = self.root / '独立 数据'
        with patch.object(entry, 'launch') as launch, redirect_stdout(StringIO()):
            self.assertEqual(entry.main(['--data-root', str(target), '--no-browser', '--port', '18765']), 0)
        self.assertEqual(launch.call_args.args[0].root, target)
        self.assertEqual(launch.call_args.args[1], 18765)
        self.assertFalse(launch.call_args.kwargs['open_page'])
        self.assertFalse(target.exists())

    def test_relative_override_fails_without_launching(self):
        with patch.object(entry, 'launch') as launch, redirect_stderr(StringIO()):
            self.assertEqual(entry.main(['--data-root', 'relative']), 1)
        launch.assert_not_called()

    def test_diagnostic_mode_does_not_launch_workbench(self):
        with patch.object(entry, 'diagnose', return_value={'frozen': True}) as diagnose, patch.object(entry, 'launch') as launch, redirect_stdout(StringIO()) as out:
            self.assertEqual(entry.main(['--data-root', str(self.root), '--diagnose']), 0)
        self.assertIn('true', out.getvalue())
        diagnose.assert_called_once()
        launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
