"""Exercise recommendation runner routing and credential fetches without cloud access."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
HELM = os.environ.get("HELM", "helm")
BASH = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")


class RecommendationRunner(unittest.TestCase):
    @classmethod
    def render(cls, *extra):
        rendered = subprocess.check_output([HELM, "template", "hopcharlie-platform",
            str(ROOT / "helm/charts/hopcharlie-platform"), "-f",
            str(ROOT / "clusters/aws-dev-management/values/hopcharlie-platform.yaml"), *extra],
            text=True, encoding="utf-8")
        return [d for d in yaml.safe_load_all(rendered) if d]

    @classmethod
    def setUpClass(cls):
        docs = cls.render()
        cls.fetch = next(d["data"]["fetch.sh"] for d in docs
            if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "hopcharlie-hopai-fetch")
        cls.job = yaml.safe_load(next(d["data"]["job.yaml"] for d in docs
            if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "hopcharlie-hopai-job"))
        cls.pod = cls.job["spec"]["template"]["spec"]
        cls.entrypoint = cls.pod["containers"][0]["args"][0]

    def test_runner_template_or_image_changes_roll_out_the_api_template_cache(self):
        def checksum(docs):
            api = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "hopcharlie-api")
            return api["spec"]["template"]["metadata"].get("annotations", {}).get("checksum/hopai")
        original = checksum(self.render())
        self.assertRegex(original or "", r"^[a-f0-9]{64}$")
        self.assertEqual(checksum(self.render()), original)
        for setting in ("hopai.maxRuns=9", "hopai.image=fixture.invalid/hopai:changed"):
            with self.subTest(setting=setting):
                self.assertNotEqual(checksum(self.render("--set-string", setting)), original)

    def test_support_marker_belongs_to_job_metadata(self):
        self.assertEqual(self.job["metadata"].get("annotations", {}).get(
            "hopcharlie.dev/recommendations"), "v1")
        self.assertNotIn("KIND", {v["name"] for v in self.pod["initContainers"][0]["env"]})
        self.assertFalse(self.pod["automountServiceAccountToken"])

    def test_rendered_shell_syntax(self):
        with tempfile.TemporaryDirectory(prefix="hopai-shell-syntax-") as directory:
            for name, shell, script in (("fetch.sh", BASH, self.fetch), ("entrypoint.sh", "sh", self.entrypoint)):
                path = Path(directory) / name
                path.write_text(script, encoding="utf-8", newline="\n")
                command = [BASH, "-c", 'sh -n "$1"', "syntax", path.as_posix()] if shell == "sh" else [shell, "-n", str(path)]
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def execute(self, kind=None, failure=""):
        with tempfile.TemporaryDirectory(prefix="hopai-recommendations-") as directory:
            work = Path(directory)
            config = work / "config"
            config.mkdir()
            (work / "aws").write_text('''#!/bin/sh
set -eu
parameter=
while [ "$#" -gt 0 ]; do
  case "$1" in --name) parameter="$2"; shift ;; esac
  shift
done
printf '%s\\n' "$parameter" >> "$CALL_LOG"
case "$parameter" in
  */hopai/kube-eks|*/hopai/kube-gke) printf fixture-kube ;;
  */hopai/anthropic-api-key) printf '%s' "$TEST_AI_KEY" ;;
  */platform-tokens)
    case "$FETCH_FAILURE" in
      denied) exit 33 ;;
      missing-agent) printf viewer:viewer:fixture-viewer ;;
      *) printf 'other:viewer:fixture-viewer,runner:agent:%s' "$TEST_AGENT_TOKEN" ;;
    esac ;;
  *) exit 92 ;;
esac
''', encoding="utf-8", newline="\n")
            (work / "hopai").write_text('''#!/bin/sh
set -eu
[ "$ANTHROPIC_API_KEY" = "$TEST_AI_KEY" ]
[ "$PLATFORM_TOKEN" = "$TEST_AGENT_TOKEN" ]
printf '%s\\n' "$@" > "$COMMAND_LOG"
''', encoding="utf-8", newline="\n")
            for name in ("aws", "hopai"):
                (work / name).chmod(0o700)
            environment = dict(os.environ, PREFIX="/fixture", CALL_LOG=(work / "calls").as_posix(),
                COMMAND_LOG=(work / "command").as_posix(), FETCH_FAILURE=failure,
                TEST_AI_KEY="fixture-ai-key-sensitive", TEST_AGENT_TOKEN="fixture-agent:token-sensitive",
                HOP_RECOMMENDATION_ID="fixture-recommendation", HOP_RECOMMENDATION_INPUT="fixture-input-sensitive",
                HOP_RECOMMENDATION_NONCE="fixture-nonce-sensitive", APP="fixture-app", FROM="eks", TO="gke",
                PATH=str(work) + os.pathsep + os.environ["PATH"])
            environment.pop("KIND", None)
            if kind is not None:
                environment["KIND"] = kind
            script = work / "fetch.sh"
            script.write_text(self.fetch.replace("/config", config.as_posix()), encoding="utf-8", newline="\n")
            fetched = subprocess.run([BASH, str(script)], cwd=work, env=environment,
                capture_output=True, text=True)
            output = fetched.stdout + fetched.stderr
            calls = (work / "calls").read_text().splitlines()
            files = {p.name for p in config.iterdir()}
            command = []
            if fetched.returncode == 0:
                main = subprocess.run([BASH, "-c", self.entrypoint.replace("/config", config.as_posix())],
                    cwd=work, env=environment, capture_output=True, text=True)
                self.assertEqual(main.returncode, 0, main.stderr)
                output += main.stdout + main.stderr
                command = (work / "command").read_text().splitlines()
            for name in ("TEST_AI_KEY", "TEST_AGENT_TOKEN", "HOP_RECOMMENDATION_INPUT", "HOP_RECOMMENDATION_NONCE"):
                self.assertNotIn(environment[name], output)
            return fetched.returncode, calls, files, command

    def test_recommend_reads_only_ai_key_and_callback_token_and_runs_recommend(self):
        code, calls, files, command = self.execute("recommend")
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["/fixture/hopai/anthropic-api-key", "/fixture/platform-tokens"])
        self.assertEqual(files, {"hopai.env"})
        self.assertEqual(command, ["recommend"])

    def test_run_and_onboard_keep_existing_cluster_fetch_and_command(self):
        for kind in (None, "onboard"):
            with self.subTest(kind=kind):
                code, calls, files, command = self.execute(kind)
                self.assertEqual(code, 0)
                self.assertEqual(calls, ["/fixture/hopai/kube-eks", "/fixture/hopai/kube-gke", "/fixture/hopai/kube-onprem",
                    "/fixture/hopai/anthropic-api-key", "/fixture/platform-tokens"])
                self.assertEqual(files, {"eks.yaml", "gke.yaml", "hopai.env"})
                self.assertEqual(command if kind else command[:1], ["onboard"] if kind else ["run"])

    def test_missing_or_denied_token_fails_before_executing_ai_without_logging_values(self):
        for failure in ("missing-agent", "denied"):
            with self.subTest(failure=failure):
                code, calls, files, command = self.execute("recommend", failure)
                self.assertNotEqual(code, 0)
                self.assertEqual(len(calls), 2)
                self.assertNotIn("hopai.env", files)
                self.assertEqual(command, [])


if __name__ == "__main__":
    unittest.main()
