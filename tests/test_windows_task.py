"""系统任务契约/保存同步/无窗口入口；测试替身不注册真实系统任务。"""
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request
import xml.etree.ElementTree as ET

from fundlab.collectors.session import ProfileLock
from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.scheduling.settings import ScheduleSettings
from fundlab.scheduling.windows_task import WindowsTask, NS, system_task_status
from fundlab.scheduling.worker import main as worker_main
from fundlab.web.server import make_server
from test_scheduling import MONDAY


class WindowsTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = AppConfig(Path(self.temp.name))
        self.task = WindowsTask(self.config, interpreter=Path(__file__))
        self.missing = {'installed':False, 'sid':'S-1-test'}

    def raw(self, time='18:00', enabled=True):
        return dict(installed=True, sid='S-1-test', xml=self.task.xml(time, 'S-1-test', enabled=enabled, now=MONDAY),
                    enabled=enabled, state=3, last_result=0, last_run='2026-10-19T18:00:00', next_run='2026-10-20T18:00:00')

    def test_xml_is_interactive_lowest_no_password_no_wake_and_no_periodic_loop(self):
        root = ET.fromstring(self.task.xml('20:30','S-1-test',now=MONDAY))
        get = lambda path: root.findtext('/'.join('{'+NS+'}'+part for part in path.split('/')))
        self.assertEqual(get('Principals/Principal/LogonType'),'InteractiveToken')
        self.assertEqual(get('Principals/Principal/RunLevel'),'LeastPrivilege')
        self.assertEqual(get('Settings/WakeToRun'),'false')
        self.assertEqual(get('Settings/AllowHardTerminate'),'false')
        self.assertEqual(get('Settings/MultipleInstancesPolicy'),'IgnoreNew')
        self.assertEqual(get('Settings/RestartOnFailure/Count'),'2')
        self.assertEqual(get('Settings/RestartOnFailure/Interval'),'PT5M')
        self.assertTrue(get('Triggers/CalendarTrigger/StartBoundary').endswith('20:30:00+08:00'))
        self.assertIsNone(get('Triggers/BootTrigger'))
        self.assertIsNone(get('Triggers/CalendarTrigger/Repetition'))
        self.assertEqual(len(root.findall('{'+NS+'}Triggers/*')),4)
        self.assertEqual(get('Actions/Exec/Command'),str(Path(__file__).resolve()))
        self.assertNotIn('Password',self.task.xml('20:30','S-1-test'))

    def test_name_and_action_are_bound_to_canonical_project(self):
        other=WindowsTask(AppConfig(self.config.root/'other'),interpreter=Path(__file__))
        self.assertNotEqual(self.task.name,other.name)
        self.assertIn(str(self.config.root),self.task.arguments)

    def test_query_does_not_write_and_missing_is_not_enabled(self):
        with patch.object(self.task,'query',return_value=self.missing):
            state=self.task.state('18:00')
        self.assertFalse(state['installed']); self.assertFalse(state['enabled'])
        self.assertEqual(list(self.config.root.iterdir()),[])

    def test_enabled_disabled_and_drift_are_reported(self):
        for clock,enabled,healthy in [('18:00',True,True),('18:00',False,True),('20:30',True,False)]:
            with patch.object(self.task,'query',return_value=self.raw(clock,enabled)):
                state=self.task.state('18:00')
            self.assertEqual(state['healthy'],healthy)
            self.assertEqual(state['enabled'],enabled)

    def test_changed_privileges_and_extra_action_never_healthy(self):
        for old,new in [('LeastPrivilege','HighestAvailable'),('<DaysInterval>1</DaysInterval>','<DaysInterval>7</DaysInterval>'),('</Actions>','<Exec><Command>other.exe</Command></Exec></Actions>')]:
            raw=self.raw(); raw['xml']=raw['xml'].replace(old,new)
            with patch.object(self.task,'query',return_value=raw):
                self.assertFalse(self.task.state('18:00')['healthy'])

    def test_windows_normalized_default_values_order_and_account_names(self):
        raw=self.raw()
        raw['xml']=raw['xml'].replace('<RunLevel>LeastPrivilege</RunLevel>','').replace('<WakeToRun>false</WakeToRun>','').replace('<Enabled>true</Enabled>','')
        # Windows将触发器中的SID写成账户名，主Principal仍为SID。
        raw['xml']=raw['xml'].replace('<UserId>S-1-test</UserId>','<UserId>TEST\\user</UserId>',2)
        raw['trigger_users']=['S-1-test','S-1-test']
        with patch.object(self.task,'query',return_value=raw): self.assertTrue(self.task.state('18:00')['healthy'])

    def test_trigger_other_user_is_unhealthy(self):
        raw=self.raw();raw['trigger_users']=['S-1-other','S-1-test']
        with patch.object(self.task,'query',return_value=raw): self.assertFalse(self.task.state('18:00')['healthy'])

    def test_transport_conflict_never_silently_overwrites_existing_task(self):
        from fundlab.scheduling.windows_task import transport
        from types import SimpleNamespace
        reply=SimpleNamespace(returncode=1,stdout=json.dumps({'ok':False,'error':'TASK_OWNERSHIP_CONFLICT','hresult':0}))
        with patch('fundlab.scheduling.windows_task.subprocess.run',return_value=reply) as run,self.assertRaises(FundlabError) as caught:
            transport({'operation':'query','name':'test'})
        self.assertEqual(caught.exception.code,'WINDOWS_TASK_CONFLICT')
        self.assertEqual(run.call_count,1)
        self.assertNotIn('shell',run.call_args.kwargs)

    def test_transport_timeout_is_unconfirmed_and_does_not_repeat_mutation(self):
        from fundlab.scheduling.windows_task import transport
        import subprocess
        with patch('fundlab.scheduling.windows_task.subprocess.run',side_effect=subprocess.TimeoutExpired('test',30)) as run,self.assertRaises(FundlabError):
            transport({'operation':'register','name':'test'})
        self.assertEqual(run.call_count,1)

    def test_cli_exposes_explicit_task_management(self):
        from fundlab.cli import main
        for action in ['install','disable','remove']:
            with patch('sys.argv',['fundlab','schedule',action]),patch('fundlab.cli.AppConfig.default',return_value=self.config),patch('fundlab.scheduling.windows_task.WindowsTask.change',return_value={}) as change,patch('builtins.print'):
                self.assertEqual(main(),0)
            change.assert_called_once_with(action)

    def test_permission_failure_is_visible_not_treated_as_absent(self):
        with patch('fundlab.scheduling.windows_task.WindowsTask.query',side_effect=FundlabError('WINDOWS_TASK_FAILED','test')):
            state=system_task_status(self.config,'18:00')
        self.assertIsNone(state['installed']); self.assertFalse(state['healthy'])

    def test_unknown_action_never_calls_system(self):
        with patch.object(self.task,'call') as call,self.assertRaises(FundlabError):
            self.task.change('remove-everything')
        call.assert_not_called()

    def test_active_batch_blocks_system_mutations(self):
        with ProfileLock(self.config.local_dir/'scheduler.lock'),patch.object(self.task,'query') as query,self.assertRaises(FundlabError):
            self.task.change('disable')
        query.assert_not_called()

    def test_install_and_disable_remove_only_own_identity(self):
        for action in ['install','disable','remove']:
            with patch.object(self.task,'query',return_value=self.missing),patch.object(self.task,'register') as register,patch.object(self.task,'call') as call,patch.object(self.task,'state',return_value={}) as state:
                self.task.change(action)
            if action=='install':
                register.assert_called_once_with('18:00',self.missing,enabled=True)
            else: call.assert_called_once_with(action)
            state.assert_called_once_with('18:00')
        self.assertFalse(self.config.database_path.exists())

    def test_registered_clock_sync_preserves_disabled_and_has_no_collection(self):
        with patch('fundlab.scheduling.windows_task.WindowsTask.query',return_value=self.raw(enabled=False)),patch('fundlab.scheduling.windows_task.WindowsTask.register') as register,patch('fundlab.scheduling.runner.update_one') as collect:
            ScheduleSettings(self.config).save('20:30')
        self.assertEqual(register.call_args.args[0],'20:30')
        self.assertFalse(register.call_args.kwargs['enabled'])
        collect.assert_not_called()
        self.assertEqual(ScheduleSettings(self.config).read()['update_time'],'20:30')

    def test_failed_task_sync_does_not_publish_new_clock(self):
        with patch('fundlab.scheduling.windows_task.WindowsTask.query',return_value=self.raw()),patch('fundlab.scheduling.windows_task.WindowsTask.register',side_effect=FundlabError('WINDOWS_TASK_FAILED','test')),self.assertRaises(FundlabError):
            ScheduleSettings(self.config).save('20:30')
        self.assertFalse(ScheduleSettings(self.config).path.exists())
        self.assertEqual(list(self.config.data_dir.glob('*.tmp')),[])

    def test_settings_publish_failure_restores_previous_task_xml(self):
        before=self.raw()
        with patch('fundlab.scheduling.windows_task.WindowsTask.query',return_value=before),patch('fundlab.scheduling.windows_task.WindowsTask.register'),patch('fundlab.scheduling.windows_task.WindowsTask.call') as call,patch('fundlab.scheduling.settings.os.replace',side_effect=OSError('test')),self.assertRaises(FundlabError):
            ScheduleSettings(self.config).save('20:30')
        call.assert_called_once_with('register',xml=before['xml'])
        self.assertFalse(ScheduleSettings(self.config).path.exists())

    def test_rollback_failure_requires_explicit_verification(self):
        with patch('fundlab.scheduling.windows_task.WindowsTask.query',return_value=self.raw()),patch('fundlab.scheduling.windows_task.WindowsTask.register'),patch('fundlab.scheduling.windows_task.WindowsTask.call',side_effect=FundlabError('WINDOWS_TASK_FAILED','test')),patch('fundlab.scheduling.settings.os.replace',side_effect=OSError('test')),self.assertRaises(FundlabError) as caught:
            ScheduleSettings(self.config).save('20:30')
        self.assertEqual(caught.exception.code,'SCHEDULE_SYNC_UNCONFIRMED')

    def test_worker_exit_and_result_log_use_same_executor(self):
        for status,code in [('not_due',0),('skipped_closed',0),('succeeded',0),('pending',2)]:
            with patch('fundlab.scheduling.worker.run_due_updates',return_value={'status':status}) as run:
                self.assertEqual(worker_main(['--project-root',str(self.config.root)]),code)
            run.assert_called_once_with(self.config)
        records=[json.loads(line) for line in (self.config.local_dir/'scheduled-worker.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(records),8)
        self.assertEqual(records[-1]['exit_code'],2)

    def test_worker_records_failure_and_no_browser_login_loop(self):
        with patch('fundlab.scheduling.worker.run_due_updates',side_effect=FundlabError('AUTH_REQUIRED','请本人登录')):
            self.assertEqual(worker_main(['--project-root',str(self.config.root)]),1)
        self.assertIn('AUTH_REQUIRED',(self.config.local_dir/'scheduled-worker.jsonl').read_text(encoding='utf-8'))

    def test_worker_cannot_log_does_not_collect(self):
        with patch('pathlib.Path.open',side_effect=OSError('test')),patch('fundlab.scheduling.worker.run_due_updates') as run:
            self.assertEqual(worker_main(['--project-root',str(self.config.root)]),1)
        run.assert_not_called()

    def test_http_mutations_require_local_csrf_exact_fields_and_have_no_get_action(self):
        server=make_server(self.config,port=0); thread=Thread(target=server.serve_forever,daemon=True); thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join(2)))
        base=f'http://127.0.0.1:{server.server_port}'; opener=build_opener(ProxyHandler({}))
        with opener.open(base+'/api/session') as response: token=json.load(response)['csrf_token']
        with patch('fundlab.scheduling.windows_task.WindowsTask.change') as change,patch('fundlab.scheduling.windows_task.WindowsTask.query',return_value=self.missing):
            for action in ['install','disable','remove']:
                path=base+'/api/schedule/'+action
                for body,headers in [({},{}),({'extra':True},{'X-Fundlab-CSRF':token}),({}, {'X-Fundlab-CSRF':token,'Origin':'https://outside.invalid'})]:
                    with self.assertRaises(HTTPError): opener.open(Request(path,json.dumps(body).encode(),{'Content-Type':'application/json',**headers}))
                change.assert_not_called()
                with self.assertRaises(HTTPError) as caught: opener.open(path)
                self.assertEqual(caught.exception.code,404)
                with opener.open(Request(path,b'{}',{'Content-Type':'application/json','X-Fundlab-CSRF':token,'Origin':base})) as response:
                    self.assertEqual(response.status,200)
                change.assert_called_once_with(action); change.reset_mock()
        self.assertFalse(self.config.database_path.exists())


if __name__=='__main__': unittest.main()
