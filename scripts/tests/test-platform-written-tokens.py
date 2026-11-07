"""Exercise the rendered settings script without cloud access or real credentials."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
HELM = os.environ.get("HELM", "helm")
BASH = ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt"
        else shutil.which("bash"))


class WrittenTokenSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rendered = subprocess.check_output([HELM, "template", "hopcharlie-platform",
            str(ROOT / "helm/charts/hopcharlie-platform"), "-f",
            str(ROOT / "clusters/aws-dev-management/values/hopcharlie-platform.yaml")],
            text=True, encoding="utf-8")
        docs = [d for d in yaml.safe_load_all(rendered) if d]
        cls.script = next(d["data"]["fetch-settings.sh"]
            for d in docs
            if d and d["kind"] == "ConfigMap" and d["metadata"]["name"] == "hopcharlie-fetch-settings")
        api = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "hopcharlie-api")
        cls.entrypoint = api["spec"]["template"]["spec"]["containers"][0]["args"][0]

    def execute(self, mode, token="app-demo:" + "fixture-only-token-" * 2, fetch="api"):
        with tempfile.TemporaryDirectory(prefix="written-token-settings-") as directory:
            work = Path(directory)
            config = work / "config"
            config.mkdir()
            fake = work / "aws"
            fake.write_text('''#!/bin/sh
set -eu
case "$*" in
  *"get-parameters "*)
    case "$*" in
      *"/written-record-tokens"*)
        case "$WRITTEN_TEST_MODE" in
          denied) echo AccessDeniedException >&2; exit 42 ;;
          missing) printf None ;;
          empty) : ;;
          configured) printf '%s' "$WRITTEN_TEST_TOKEN" ;;
          *) exit 90 ;;
        esac ;;
      *) printf None ;;
    esac ;;
  *"get-parameters-by-path"*) printf None ;;
  *"get-parameter "*) printf fixture ;;
  *) exit 91 ;;
esac
''', encoding="utf-8", newline="\n")
            fake.chmod(0o700)
            api_binary = work / "hopcharlie-api"
            api_binary.write_text('#!/bin/sh\nprintf \'%s\' "${PLATFORM_WRITTEN_RECORD_TOKENS:-UNSET}"\n',
                encoding="utf-8", newline="\n")
            api_binary.chmod(0o700)
            script = work / "fetch-settings.sh"
            script.write_text(self.script.replace("/config", config.as_posix()), encoding="utf-8", newline="\n")
            environment = dict(os.environ, FETCH=fetch, PREFIX="/fixture", GITHUB_APP_ID="",
                WRITTEN_TEST_MODE=mode, WRITTEN_TEST_TOKEN=token,
                PATH=str(work) + os.pathsep + os.environ["PATH"])
            result = subprocess.run([BASH, str(script)], cwd=work, env=environment,
                capture_output=True, text=True)
            self.assertNotIn(token, result.stdout + result.stderr)
            env_file = config / "api.env"
            content = env_file.read_text() if env_file.exists() else ""
            if result.returncode == 0 and env_file.exists():
                # Execute the real rendered consumer too: it exports literal
                # KEY=value lines; sourcing the file would test a different contract.
                loaded = subprocess.run([BASH, "-c", self.entrypoint.replace("/config", config.as_posix())],
                    cwd=work, env=environment,
                    capture_output=True, text=True, check=True).stdout
                self.assertFalse((work / "injected-by-token").exists())
            else:
                loaded = None
            return result, content, loaded

    def test_present_parameter_reaches_only_the_api_environment(self):
        token = "app-demo:" + "fixture-only-token-" * 2
        result, content, loaded = self.execute("configured", token)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(loaded, token)
        self.assertIn("PLATFORM_WRITTEN_RECORD_TOKENS=", content)

    def test_missing_or_empty_parameter_keeps_existing_startup(self):
        for mode in ("missing", "empty"):
            with self.subTest(mode=mode):
                result, content, loaded = self.execute(mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("PLATFORM_WRITTEN_RECORD_TOKENS=", content)
                self.assertEqual(loaded, "UNSET")

    def test_access_denial_stops_fetch_instead_of_disabling_authentication(self):
        result, content, loaded = self.execute("denied")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AccessDeniedException", result.stderr)
        self.assertEqual(content, "")
        self.assertIsNone(loaded)

    def test_shell_characters_remain_a_literal_credential(self):
        for suffix in ("'; touch injected-by-token; #", "; touch injected-by-token; #"):
            with self.subTest(suffix=suffix):
                token = "app-demo:" + "fixture-only-token-" * 2 + suffix
                result, content, loaded = self.execute("configured", token)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(loaded, token)

    def test_gateway_and_postgres_do_not_fetch_the_append_credentials(self):
        for fetch in ("gateway", "postgres"):
            with self.subTest(fetch=fetch):
                result, content, loaded = self.execute("denied", fetch=fetch)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(content, "")
                self.assertIsNone(loaded)


if __name__ == "__main__":
    unittest.main()
