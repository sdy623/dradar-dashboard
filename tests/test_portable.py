import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from zhongce_radar import radar
from zhongce_radar import radar_tui as ui


class PortableTests(unittest.TestCase):
    def test_default_home_uses_user_environment_not_install_directory(self):
        from zhongce_radar.paths import data_home
        with patch.dict(os.environ, {'DRADAR_HOME': '/custom/data'}):
            self.assertEqual(data_home(), Path('/custom/data').resolve())

    def test_cli_defaults_to_codex_and_supports_python_module(self):
        result = subprocess.run([sys.executable, '-m', 'zhongce_radar', '--version'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('0.1.0', result.stdout)

    def test_public_models_work_without_private_relay_or_model_cli(self):
        with tempfile.TemporaryDirectory() as folder, patch('zhongce_radar.paths.read_settings', return_value={}):
            table={'combos':[{'model':'gpt-test','effort':'high'}]}
            self.assertEqual(ui.supported_models('codex',table,Path(folder)),{'gpt-test'})

    def test_explicit_model_list_hides_unsupported_models(self):
        with patch('zhongce_radar.paths.read_settings', return_value={'models':{'codex':['gpt-a']}}):
            table={'combos':[{'model':'gpt-a','effort':'high'},{'model':'gpt-b','effort':'max'}]}
            self.assertEqual(ui.supported_models('codex',table),{'gpt-a'})

    def test_runtime_preserves_literal_arguments_without_shell(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture=Path(folder)/'runtime.py'
            fixture.write_text('import json,sys,os;print(json.dumps({"args":sys.argv[1:],"utf8":os.getenv("PYTHONUTF8")}))',encoding='utf-8')
            settings={'runtime':{'command':[sys.executable,str(fixture)],'cwd':folder}}
            with patch('zhongce_radar.paths.read_settings',return_value=settings), patch.object(radar, 'HOME', Path(folder)), patch.object(radar, 'ROOT', Path(folder)):
                rc,output=radar.dispatch('query',['progress','--plan','日本語 $(literal); spaces','--json'])
            self.assertEqual(rc,0)
            value=json.loads(output)
            self.assertEqual(value['args'][2],'日本語 $(literal); spaces')
            self.assertEqual(value['utf8'],'1')

    def test_unconfigured_runtime_never_falls_back(self):
        with patch('zhongce_radar.paths.read_settings',return_value={}), patch('subprocess.Popen',side_effect=AssertionError('spawned')):
            with self.assertRaises(radar.RadarError):
                radar.dispatch('control',['run'])

    def test_posix_utf8_keystroke_is_not_decoded_one_byte_at_a_time(self):
        self.assertEqual(ui.decode_utf8_input(iter('我'.encode('utf-8'))),'我')


if __name__=='__main__': unittest.main()
