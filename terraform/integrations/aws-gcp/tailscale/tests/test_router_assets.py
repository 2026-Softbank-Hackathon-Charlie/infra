"""Credential-free checks for the router's network and enrollment boundaries."""

import ipaddress
import json
from pathlib import Path
import re
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INPUTS = {"aws_gateway_cidr": "10.40.0.0/19", "gcp_app_cidr": "10.80.0.0/20"}


class RouterAssetsTest(unittest.TestCase):
    def asset(self, name):
        path = ROOT / name
        self.assertTrue(path.is_file(), f"Missing router asset: {name}")
        return path.read_bytes().decode("utf-8")

    def test_only_management_to_gcp_http_is_granted(self):
        text = self.asset("policy.json.tftpl")
        for key, value in INPUTS.items():
            text = text.replace("${" + key + "}", value)
        policy = json.loads(text)
        self.assertEqual(policy["acls"], [])
        self.assertEqual(policy["ssh"], [])
        self.assertEqual(len(policy["grants"]), 1)
        grant = policy["grants"][0]
        self.assertEqual(grant["src"], [INPUTS["aws_gateway_cidr"]])
        self.assertEqual(grant["dst"], [INPUTS["gcp_app_cidr"]])
        self.assertEqual(grant["ip"], ["tcp:80"])
        self.assertNotIn("app", grant)
        self.assertFalse(ipaddress.ip_network("10.81.0.0/16").overlaps(
            ipaddress.ip_network(grant["dst"][0])))
        for port in (22, 443, 5252, 5432, 9901):
            self.assertNotIn(f"tcp:{port}", grant["ip"])

    def test_only_each_router_can_auto_approve_its_own_route(self):
        text = self.asset("policy.json.tftpl")
        for key, value in INPUTS.items():
            text = text.replace("${" + key + "}", value)
        policy = json.loads(text)
        self.assertEqual(policy["autoApprovers"], {"routes": {
            INPUTS["aws_gateway_cidr"]: ["tag:charlie-aws-router"],
            INPUTS["gcp_app_cidr"]: ["tag:charlie-gcp-router"],
        }})
        self.assertEqual(policy["tagOwners"], {
            "tag:charlie-aws-router": ["autogroup:admin"],
            "tag:charlie-gcp-router": ["autogroup:admin"],
        })

    def bash(self):
        git_bash = Path("C:/Program Files/Git/bin/bash.exe")
        binary = str(git_bash) if git_bash.is_file() else shutil.which("bash")
        if not binary:
            self.skipTest("Bash is required for bootstrap checks")
        return binary

    def rendered_bootstrap(self):
        text = self.asset("router-bootstrap.sh.tftpl")
        for key, value in {
            "hostname": "charlie-aws-app-router", "advertise_cidr": "10.40.0.0/19",
            "advertise_tag": "tag:charlie-aws-router", "web_listen": "127.0.0.1:5252",
            "remote_cidr": "10.80.0.0/20",
        }.items():
            text = text.replace("${" + key + "}", value)
        return text

    def test_generated_bootstrap_has_valid_shell_syntax(self):
        result = subprocess.run([self.bash(), "-n"], input=self.rendered_bootstrap(),
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_forwarding_installs_main_table_guard_before_tunnel_start(self):
        script = re.search(r"<<'FORWARDING'\n(.*?)\nFORWARDING", self.rendered_bootstrap(), re.S).group(1)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, body in {"ip": "printf '%s\\n' \"$@\" > \"$PROBE_ARGS\"", "iptables": "exit 0"}.items():
                path = directory / name
                path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8", newline="\n")
                path.chmod(0o700)
                script = script.replace(name + " ", f"'{path.as_posix()}' ")
            path = directory / "forward.sh"
            path.write_text(script, encoding="utf-8", newline="\n")
            arguments = directory / "args"
            result = subprocess.run([self.bash(), path.as_posix()], text=True, capture_output=True,
                                    env=dict(os.environ, PROBE_ARGS=arguments.as_posix()))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(arguments.exists(), "A down tunnel must not fall back to the VPC default route")
            self.assertEqual(arguments.read_text(encoding="utf-8").splitlines(), [
                "-4", "route", "replace", "unreachable", "10.80.0.0/20", "table", "main", "metric", "42760",
            ])

    def test_official_apt_sources_use_https_without_opening_internet_http(self):
        match = re.search(r'(sed -i \\\n.*?"\$sources_file")', self.rendered_bootstrap(), re.S)
        self.assertIsNotNone(match, "HTTPS-only routers must not bootstrap through HTTP APT mirrors")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sources.list"
            path.write_text("deb http://asia-northeast3.gce.archive.ubuntu.com/ubuntu noble main\n"
                            "URIs: http://deb.debian.org/debian http://security.debian.org/debian-security\n"
                            "deb http://packages.cloud.google.com/apt google-compute-engine main\n"
                            "deb http://security.ubuntu.com/ubuntu noble-security main\n"
                            "# http://unrelated.example/repository\n", encoding="utf-8", newline="\n")
            script = "sources_file='" + path.as_posix() + "'\n" + match.group(1)
            result = subprocess.run([self.bash(), "-s"], input=script, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            actual = path.read_text(encoding="utf-8")
            for expected in ("https://archive.ubuntu.com/ubuntu", "https://deb.debian.org/debian",
                             "https://security.debian.org/debian-security", "https://packages.cloud.google.com/apt",
                             "https://security.ubuntu.com/ubuntu", "http://unrelated.example/repository"):
                self.assertIn(expected, actual)

    def test_join_waits_for_browser_without_leaking_link_or_hiding_other_failure(self):
        script = re.search(r"<<'JOIN'\n(.*?)\nJOIN", self.rendered_bootstrap(), re.S).group(1)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fake = directory / "tailscale"
            fake.write_text("#!/bin/sh\ncase \"$1\" in\n"
                            "up) printf '%s\\n' \"$@\" > \"$PROBE_ARGS\"; "
                            "echo 'https://login.tailscale.com/a/fake' >&2; exit 1;;\n"
                            "status) printf '{\"BackendState\":\"%s\"}\\n' \"$PROBE_BACKEND\";;\nesac\n",
                            encoding="utf-8", newline="\n")
            fake.chmod(0o700)
            path = directory / "join.sh"
            script = script.replace("/usr/local/bin/tailscale", f"'{fake.as_posix()}'")
            script = script.replace("python3", f"'{Path(sys.executable).as_posix()}'")
            path.write_text(script,
                            encoding="utf-8", newline="\n")
            for state, expected in (("NeedsLogin", 0), ("NeedsMachineAuth", 0), ("Stopped", 1)):
                with self.subTest(state=state):
                    environment = dict(os.environ, PROBE_BACKEND=state,
                                       PROBE_ARGS=(directory / "args").as_posix())
                    result = subprocess.run([self.bash(), path.as_posix()], env=environment,
                                            text=True, capture_output=True)
                    self.assertEqual(result.returncode, expected, result.stderr)
                    self.assertNotIn("login.tailscale.com", result.stdout + result.stderr)
            arguments = (directory / "args").read_text(encoding="utf-8").splitlines()
            for required in ("--advertise-routes=10.40.0.0/19", "--accept-routes",
                             "--snat-subnet-routes=false", "--ssh=false", "--accept-dns=false"):
                self.assertIn(required, arguments)
            self.assertFalse(any("auth-key" in value or "advertise-exit-node" in value
                                 for value in arguments))


if __name__ == "__main__":
    unittest.main()
