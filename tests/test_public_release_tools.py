"""公开文件/源码包的隐私边界；只使用临时目录，不接触真实资料。"""
from contextlib import redirect_stderr
import importlib.util
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0,str(SCRIPTS))
from check_public_files import inspect, public_files
import build_source_archive


class PublicReleaseToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='QFund public release ')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'packaging').mkdir()
        (self.root/'src').mkdir()
        (self.root/'README.md').write_text('Public documentation',encoding='utf-8')
        (self.root/'LICENSE').write_text('Public license fixture',encoding='utf-8')
        (self.root/'pyproject.toml').write_text('[project]\nversion="1.2.3"\n',encoding='utf-8')
        (self.root/'src/app.py').write_text('value = 1\n',encoding='utf-8')
        (self.root/'packaging/public-files.json').write_text(json.dumps(dict(schema=1,
            root_files=['README.md','LICENSE','pyproject.toml'],trees={'src':['.py','.json']},docs=[])),encoding='utf-8')
        (self.root/'.local').mkdir()
        (self.root/'.local/private.txt').write_text('private sentinel',encoding='utf-8')

    def test_private_directory_is_never_selected(self):
        paths,findings,tracked=inspect(self.root)
        self.assertFalse(findings)
        self.assertFalse(tracked)
        self.assertNotIn(self.root/'.local/private.txt',paths)

    def test_missing_license_blocks_default_release_check(self):
        (self.root/'LICENSE').unlink()
        with self.assertRaises(ValueError):public_files(self.root)
        self.assertTrue(public_files(self.root,require_license=False))

    def test_private_state_file_inside_source_is_rejected(self):
        (self.root/'src/cookies.json').write_text('{}',encoding='utf-8')
        with self.assertRaises(ValueError):public_files(self.root)

    def test_suspected_token_is_reported_without_echoing_value(self):
        token='ghp_'+'x'*36
        (self.root/'src/app.py').write_text('value = "'+token+'"\n',encoding='utf-8')
        _,findings,_=inspect(self.root)
        self.assertEqual(findings,[dict(file='src/app.py',line=1,kind='github-token')])
        self.assertNotIn(token,json.dumps(findings))

    def test_tracked_private_file_is_not_hidden_by_ignore_rules(self):
        (self.root/'.git').mkdir()
        with patch('check_public_files.subprocess.check_output',return_value='README.md\0data/private.sqlite3\0'):
            _,findings,tracked=inspect(self.root)
        self.assertTrue(tracked)
        self.assertEqual(findings,[dict(file='data/private.sqlite3',line=0,kind='tracked-outside-public-scope')])

    def build(self,output):
        with patch.object(build_source_archive,'__file__',str(self.root/'scripts/build_source_archive.py')), \
             patch.object(sys,'argv',['build_source_archive','--output-dir',str(output)]):
            return build_source_archive.main()

    def test_archive_excludes_private_files_and_retains_exact_source(self):
        output=self.root/'dist/new'
        self.assertEqual(self.build(output),0)
        with zipfile.ZipFile(output/'QFund-1.2.3-source.zip') as archive:
            self.assertNotIn('QFund-1.2.3/.local/private.txt',archive.namelist())
            self.assertEqual(archive.read('QFund-1.2.3/src/app.py'),(self.root/'src/app.py').read_bytes())
        self.assertTrue((output/'source-manifest.json').is_file())

    def test_archive_will_not_overwrite_previous_delivery(self):
        output=self.root/'dist/old';output.mkdir(parents=True)
        marker=output/'keep.txt';marker.write_bytes(b'previous delivery')
        with redirect_stderr(StringIO()):self.assertEqual(self.build(output),1)
        self.assertEqual(marker.read_bytes(),b'previous delivery')


if __name__=='__main__':unittest.main()
