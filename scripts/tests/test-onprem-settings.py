"""Run the real settings script with fixture-only SSM, never real credentials."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
BASH='C:/Program Files/Git/bin/bash.exe' if os.name=='nt' else shutil.which('bash')


class OnpremSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('registration_render',ROOT/'scripts/tests/test-onprem-registration.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        cls.script=next(d for d in module.render(True) if d['kind']=='ConfigMap' and d['metadata']['name']=='hopcharlie-fetch-settings')['data']['fetch-settings.sh']

    def execute(self,mode,value='',enabled=True):
        with tempfile.TemporaryDirectory(prefix='onprem-settings-') as directory:
            work=Path(directory);config=work/'config';config.mkdir()
            fake=work/'aws'
            fake.write_text('''#!/bin/sh
set -eu
case "$*" in
 *"get-parameters "*)
  case "$*" in
   *"/tailscale-onboard-oauth"*) printf '%s' '{"clientId":"legacy-client","clientSecret":"legacy-secret"}' ;;
   *) printf None ;;
  esac ;;
 *"get-parameters-by-path"*) printf None ;;
 *"get-parameter "*)
  case "$*" in
   *"/onprem-registration-oauth"*)
    printf requested >> "$FIXTURE_CALLS"
    case "$FIXTURE_MODE" in
     denied) echo AccessDeniedException >&2; exit 42 ;;
     missing) echo ParameterNotFound >&2; exit 42 ;;
     *) printf '%s' "$FIXTURE_VALUE" ;;
    esac ;;
   *"/db-password "*)
    if [ "$FIXTURE_MODE" = required_denied ]; then echo AccessDeniedException >&2; exit 42; fi
    printf fixture ;;
   *) printf fixture ;;
  esac ;;
 *) exit 90 ;;
esac
''',encoding='utf-8',newline='\n');fake.chmod(0o700)
            script=work/'fetch.sh';script.write_text(self.script.replace('/config',config.as_posix()),encoding='utf-8',newline='\n')
            env=dict(os.environ,PATH=str(work)+os.pathsep+os.environ['PATH'],FETCH='api',PREFIX='/fixture',GITHUB_APP_ID='',ONPREM_REGISTRATION='1' if enabled else '0',FIXTURE_MODE=mode,FIXTURE_VALUE=value,FIXTURE_CALLS=str(work/'calls'))
            result=subprocess.run([BASH,str(script)],env=env,capture_output=True,text=True)
            content=(config/'api.env').read_text() if (config/'api.env').exists() else ''
            self.assertNotIn('new-secret',result.stdout+result.stderr)
            self.assertNotIn('legacy-secret',result.stdout+result.stderr)
            return result,content,(work/'calls').exists()

    def test_new_optional_oauth_failure_does_not_break_existing_api_settings(self):
        for mode in ['denied','missing']:
            result,content,called=self.execute(mode)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(called)
            self.assertIn('PLATFORM_DATABASE_URL=',content)
            self.assertIn('PLATFORM_TAILSCALE_OAUTH_CLIENT_ID=legacy-client',content)
            self.assertNotIn('PLATFORM_ONPREM_TAILSCALE_OAUTH_CLIENT_ID=',content)
            self.assertIn('registration disabled',result.stderr)

    def test_only_strict_complete_json_provisions_dedicated_client(self):
        for value in ['{"clientId":"new-client","clientSecret":"new-secret"}',
                      '{ "clientSecret" : "new-secret",\n"clientId" : "new-client" }']:
            result,content,called=self.execute('configured',value)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(called)
            self.assertIn('PLATFORM_ONPREM_TAILSCALE_OAUTH_CLIENT_ID=new-client\n',content)
            self.assertIn('PLATFORM_ONPREM_TAILSCALE_OAUTH_CLIENT_SECRET=new-secret\n',content)

    def test_invalid_json_cannot_be_repaired_or_reused_as_legacy_client(self):
        for value in ['{"clientId":"new-client","clientSecret":"new-secret"} trailing',
                      '{"clientId":"new client","clientSecret":"new-secret"}',
                      '{"clientId":"new-client","clientSecret":"new-secret","extra":true}',
                      '{"clientId":"new-client","clientId":"other","clientSecret":"new-secret"}',
                      '{"clientId":"new-client"}', 'not-json']:
            result,content,_=self.execute('configured',value)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertNotIn('PLATFORM_ONPREM_TAILSCALE_OAUTH_CLIENT_ID=',content)
            self.assertIn('registration disabled',result.stderr)

    def test_disabled_does_not_query_new_parameter(self):
        result,content,called=self.execute('denied',enabled=False)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(called)
        self.assertNotIn('PLATFORM_ONPREM_TAILSCALE_',content)

    def test_required_parameter_failure_still_stops_fetch(self):
        result,_,_=self.execute('required_denied')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('AccessDeniedException',result.stderr)


if __name__=='__main__':
    unittest.main()
