"""数据备份＋隔离恢复维护入口；不联网、不覆盖原数据、不复制登录资料。"""
from contextlib import ExitStack, closing
from datetime import datetime
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import sys
from uuid import uuid4

from fundlab.collectors.session import ProfileLock
from fundlab.config import AppConfig
from fundlab.errors import FundlabError
from fundlab.scheduling.calendar import load_calendar
from fundlab.scheduling.rules import beijing, build_plan
from fundlab.scheduling.settings import ScheduleSettings
from fundlab.scheduling.store import ScheduleStore
from fundlab.scheduling.windows_task import WindowsTask
from fundlab.services.analysis import analyze_one
from fundlab.services.data_query import overview
from fundlab.services.update import update_lock
from fundlab.services.watchlist import WatchlistService


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda:file.read(1024*1024),b''): value.update(block)
    return value.hexdigest()


def write_json(path, data):
    with path.open('x',encoding='utf-8') as file:
        json.dump(data,file,ensure_ascii=False,indent=2,allow_nan=False)
        file.flush(); os.fsync(file.fileno())


def checked_file(path, root):
    link=lambda p:p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction())
    if link(path) or any(link(parent) for parent in path.parents if parent!=root.parent):
        raise FundlabError('BACKUP_LINK',f'备份不跟随链接，请先核对：{path}')
    if not path.resolve().is_relative_to(root.resolve()):
        raise FundlabError('BACKUP_PATH','备份源路径超出项目，未复制。')


def database_summary(path):
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
        foreign=db.execute('PRAGMA foreign_key_check').fetchall()
        schema=db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
        tables={}
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            quote=lambda s:'"'+s.replace('"','""')+'"'
            columns=[row[1] for row in db.execute('PRAGMA table_info('+quote(name)+')')]
            rows=db.execute('SELECT * FROM '+quote(name)+' ORDER BY '+','.join(map(quote,columns)))
            checksum=hashlib.sha256(); count=0
            for row in rows:
                checksum.update(json.dumps(row,ensure_ascii=False,separators=(',',':'),default=lambda v:v.hex()).encode('utf-8')+b'\n'); count+=1
            tables[name]={'rows':count,'sha256':checksum.hexdigest()}
        if integrity!='ok' or foreign:
            raise FundlabError('BACKUP_DATABASE_INVALID',f'数据库完整性或外键检查失败，保留原库：{path.name}')
        return dict(user_version=db.execute('PRAGMA user_version').fetchone()[0],integrity=integrity,
                    foreign_key_errors=foreign,schema=schema,tables=tables)


def business_summary(config, now):
    state=overview(config); state.pop('generated_at',None)
    watch=WatchlistService(config).state(); schedule=ScheduleStore(config).read(); settings=ScheduleSettings(config).read()
    return dict(overview=state,watchlist=watch,schedule=schedule,settings=settings,
                due_plan=build_plan(watch['items'],schedule['latest'],now,update_time=settings['update_time'],
                                    effective_from=settings['effective_from'],calendar=load_calendar()),
                analysis={fund['code']:analyze_one(config,fund['code']) for fund in state['funds']})


def selected_files(config):
    root=config.root
    direct=['README.md','使用说明.md','AGENTS.md','AI 编程项目 LLM 开发设计包.md','启动工作台.cmd',
            'pyproject.toml','requirements.lock.txt','.gitignore','data/schedule-settings.json',
            '.local/web-tasks.json','.local/scheduled-worker.jsonl']
    files={root/name for name in direct if (root/name).is_file()}
    groups=[('src',{'.py','.html','.css','.js','.json','.ps1'}),('scripts',{'.py','.cmd'}),
            ('docs',{'.md'}),('tests',{'.py'}),('tests/fixtures',None),('data/funds',None),('data/analysis',None)]
    for directory,suffixes in groups:
        parent=root/directory
        if parent.exists():
            checked_file(parent,root)
            for path in parent.rglob('*'):
                if '__pycache__' in path.parts: continue
                checked_file(path,root)
                if path.is_file() and (suffixes is None or path.suffix in suffixes): files.add(path)
    for name in ['stage6-night-acceptance-2026-10-22.json','stage6-night-system-task-2026-10-22.json',
                 'stage6-night-system-events-2026-10-22.json','stage6-night-documentation-check-2026-10-22.json']:
        path=config.evidence_dir/name
        if path.is_file(): files.add(path)
    for path in files: checked_file(path,root)
    return sorted(files,key=lambda p:str(p.relative_to(root)))


def check_web_idle(config):
    path=config.local_dir/'web-tasks.json'
    if path.exists():
        records=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(records,list): raise FundlabError('BACKUP_WEB_RECORDS','网页任务记录无法确认，未备份。')
        if any(job.get('status') in {'queued','running','waiting_login'} for job in records):
            raise FundlabError('BACKUP_BUSY','有网页任务执行或等待登录，请完成后再备份。')


def backup_and_verify(config, *, task=None):
    """仅创建全新备份/演练目录；失败保留部分文件并恢复原任务启停，不覆盖正式库。"""
    now=beijing(); stamp=now.strftime('%Y-%m-%d_%H%M%S')+'_'+uuid4().hex[:8]
    root=config.root.resolve(); config=AppConfig(root)
    backup_parent=root/'backups'; restore_parent=config.local_dir/'restore-drills'
    for parent in (backup_parent,restore_parent): checked_file(parent,root)
    backup=backup_parent/stamp; restored=restore_parent/stamp
    task=task or WindowsTask(config)
    evidence={'schema':1,'status':'incomplete','created_at':now.isoformat(),'source_root':str(root),
              'backup':str(backup),'restored':str(restored),'profile_copied':False,'network_used':False,
              'restored_task_registered':False,'restore_uses_current_interpreter':True,
              'python':platform.python_version(),'executable':sys.executable,'base_prefix':sys.base_prefix,
              'dependencies':{name:version(name) for name in ('fundlab-local','playwright','openpyxl','plotly')}}
    state_before=None; paused=False; before=None
    try:
        with ExitStack() as stack:
            stack.enter_context(ProfileLock(config.local_dir/'scheduler.lock',FundlabError('SCHEDULE_BUSY','自动更新执行中，请完成后备份。')))
            stack.enter_context(update_lock(config))
            stack.enter_context(ProfileLock(config.local_dir/'edge-profile.lock'))
            check_web_idle(config)
            settings=ScheduleSettings(config).read()
            health=task.state(settings['update_time'])
            state_before=task.query()
            if state_before['installed'] and (state_before['state']==4 or not health['healthy']):
                raise FundlabError('BACKUP_TASK_UNCONFIRMED','系统任务正在运行或配置需核对，保留原状态，未备份。')
            # 名单有独立HTTP写入口：持有SQLite保留写锁阻止名单同时改变，备份从另一只读连接读取。
            for path in (config.database_path,config.watchlist_path,config.schedule_path):
                if path.exists():
                    checked_file(path,root)
                    db=stack.enter_context(closing(sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True,timeout=0)))
                    db.execute('BEGIN IMMEDIATE')
                    stack.callback(db.rollback)
            before=business_summary(config,now)
            files=selected_files(config)
            fingerprints={str(path.relative_to(root)):digest(path) for path in files}
            databases={str(path.relative_to(root)):database_summary(path) for path in
                       (config.database_path,config.watchlist_path,config.schedule_path) if path.exists()}
            backup.mkdir(parents=True,exist_ok=False)
            payload=backup/'payload'; payload.mkdir()
            evidence.update(system_task_before=state_before,business_before=before,databases=databases,
                            omitted_databases=[str(p.relative_to(root)) for p in (config.watchlist_path,config.schedule_path) if not p.exists()],files={})
            # 在暂停之前先保存原任务状态；强制断电/终止无法执行finally时仍有恢复依据。
            write_json(backup/'operation-start.json',{'status':'started','created_at':now.isoformat(),
                       'source_root':str(root),'system_task_before':state_before,'settings':settings})
            try:
                if state_before['installed'] and state_before['enabled']:
                    paused=True  # 即使系统调用结果不明确，finally也恢复原XML。
                    task.call('disable')
                    if task.query()['enabled']: raise FundlabError('BACKUP_TASK_PAUSE','未能确认任务已暂停，停止备份。')
                for relative,summary in databases.items():
                    target=payload/relative; target.parent.mkdir(parents=True,exist_ok=True)
                    with closing(sqlite3.connect((root/relative).as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(target)) as destination:
                        source.backup(destination,pages=128,sleep=0.01)
                    if database_summary(target)!=summary: raise FundlabError('BACKUP_DATABASE_MISMATCH','数据库备份对照失败，保留部分备份。')
                for path in files:
                    relative=str(path.relative_to(root)); target=payload/relative
                    target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(path,target)
                    if digest(target)!=fingerprints[relative]: raise FundlabError('BACKUP_FILE_CHANGED','备份期间文件变化，保留部分备份，请核对。')
                all_payload_files=sorted(p for p in payload.rglob('*') if p.is_file())
                evidence['files']={str(p.relative_to(payload)):{'bytes':p.stat().st_size,'sha256':digest(p)} for p in all_payload_files}
                if {str(p.relative_to(root)):digest(p) for p in files}!=fingerprints or selected_files(config)!=files:
                    raise FundlabError('BACKUP_SOURCE_CHANGED','备份期间源文件变化，本次未标记成功。')
                if any(database_summary(root/path)!=summary for path,summary in databases.items()) or business_summary(config,now)!=before:
                    raise FundlabError('BACKUP_SOURCE_CHANGED','备份期间业务数据变化，本次未标记成功。')
                check_web_idle(config)
                evidence['production_data_unchanged']=True
            finally:
                if paused:
                    task.call('register',xml=state_before['xml'])
                state_after=task.query()
                evidence['system_task_after']=state_after
                if (state_after['installed']!=state_before['installed'] or
                    (state_before['installed'] and (state_after['enabled']!=state_before['enabled'] or state_after['xml']!=state_before['xml']))):
                    raise FundlabError('BACKUP_TASK_RESTORE','系统任务原状态恢复需核对，请保留备份和证据。')
                evidence['system_task_state_restored']=True
        # 只从备份拷贝到新的演练目录；从不将演练文件写回正式位置。
        restored.mkdir(parents=True,exist_ok=False)
        for relative,info in evidence['files'].items():
            source=payload/relative; target=restored/relative
            if digest(source)!=info['sha256']: raise FundlabError('BACKUP_TAMPERED','备份文件指纹发生变化，未继续恢复。')
            target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source,target)
            if digest(target)!=info['sha256']: raise FundlabError('RESTORE_MISMATCH','恢复文件与备份不一致，原数据未覆盖。')
        restored_config=AppConfig(restored)
        if any(database_summary(restored/path)!=summary for path,summary in databases.items()):
            raise FundlabError('RESTORE_MISMATCH','隔离恢复数据库不一致。')
        if business_summary(restored_config,now)!=before:
            raise FundlabError('RESTORE_MISMATCH','隔离数据的查询/分析/名单/调度/时间不一致。')
        if restored_config.profile_dir.exists(): raise FundlabError('RESTORE_PROFILE','演练不应包含浏览器profile。')
        evidence.update(status='verified',completed_at=beijing().isoformat(),restored_business_matches=True,
                        file_count=len(evidence['files']),payload_bytes=sum(x['bytes'] for x in evidence['files'].values()),
                        analysis_funds=len(before['analysis']),original_success_not_due=len(before['due_plan'])==0)
        write_json(backup/'manifest.json',evidence)
        (backup/'备份说明.md').write_text('这是本机数据备份，manifest.json状态verified表示本次隔离恢复核对通过。\n'
            'payload包含数据及对应源码文档，不含.venv或浏览器登录资料，不是Windows安装器。\n'
            '恢复演练使用本机当前项目解释器读取新目录，未覆盖生产库，也未向Windows注册副本任务。\n'
            '此备份在同一磁盘，不能代替外部离线备份；不要将含私人数据的备份公开上传。\n',encoding='utf-8')
        return evidence
    except BaseException as error:
        evidence['error_type']=type(error).__name__; evidence['error_code']=getattr(error,'code',None)
        evidence['message']=str(error)
        if backup.exists() and not (backup/'manifest.json').exists(): write_json(backup/'manifest.json',evidence)
        raise


def main():
    try:
        result=backup_and_verify(AppConfig(Path(__file__).resolve().parents[1]))
        print(json.dumps({key:result[key] for key in ('status','backup','restored','file_count','payload_bytes','analysis_funds','system_task_state_restored')},ensure_ascii=False,indent=2))
        return 0
    except (FundlabError,OSError,sqlite3.Error,ValueError) as error:
        print(f'备份/演练未完成：{error}\n未覆盖正式数据；部分备份保留，须核对状态后再使用。',file=sys.stderr)
        return 1


if __name__=='__main__': raise SystemExit(main())
