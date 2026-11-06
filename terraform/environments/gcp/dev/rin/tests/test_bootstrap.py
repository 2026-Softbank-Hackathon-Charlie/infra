"""Bootstrap security and restore boundaries, without root/cloud access."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RinBootstrapTests(unittest.TestCase):
    def test_metadata_guard_is_installed_before_docker_can_start(self):
        self.assertTrue((ROOT / "startup.sh").exists(), "rin bootstrap with metadata guard must exist")
        script = (ROOT / "startup.sh").read_text()
        self.assertLess(script.index("systemctl mask docker.service docker.socket"), script.index("apt-get install"))
        self.assertIn("Before=docker.service", script)
        self.assertIn("Requires=rin-metadata-guard.service", script)
        self.assertLess(script.index("systemctl start rin-metadata-guard.service"), script.index("systemctl unmask docker.service docker.socket"))
        self.assertNotIn("docker compose", script)
        self.assertNotIn(".migration-ready", script)

    def test_guard_blocks_metadata_http_and_https_and_keeps_dns(self):
        self.assertTrue((ROOT / "startup.sh").exists(), "rin bootstrap with metadata guard must exist")
        script = (ROOT / "startup.sh").read_text()
        guard = script.split("<<'GUARD'\n", 1)[1].split("\nGUARD", 1)[0]
        # Exercise the guard on Linux CI and Git Bash on Windows.
        self.assertIn("-d 169.254.169.254/32 -p tcp -m multiport --dports 80,443 -j REJECT", guard)
        self.assertNotIn("--dport 53", guard)
        self.assertIn("-C FORWARD -j DOCKER-USER", guard)
        self.assertIn("-I FORWARD 1 -j DOCKER-USER", guard)
        self.assertIn("-I DOCKER-USER 1", guard)
        bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else "bash"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "guard").write_text(guard)
            (path / "iptables").write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$TRACE"\n[ "${FAIL:-0}" != 1 ] || exit 1\ncase " $* " in *" -C "*) [ "${PRESENT:-0}" = 1 ];; *" -N "*) [ "${PRESENT:-0}" != 1 ];; esac\n')
            (path / "iptables").chmod(0o755)
            trace = path / "trace"
            subprocess.run([bash, "-eu", (path / "guard").as_posix()], check=True, env=dict(os.environ, PATH=f"{path}{os.pathsep}{os.environ['PATH']}", TRACE=trace.as_posix()))
            commands = trace.read_text().splitlines()
            self.assertTrue(any("-I FORWARD 1 -j DOCKER-USER" in c for c in commands))
            self.assertTrue(any("-I DOCKER-USER 1 -d 169.254.169.254/32" in c for c in commands))
            trace.unlink()
            env = dict(os.environ, PATH=f"{path}{os.pathsep}{os.environ['PATH']}", TRACE=trace.as_posix(), PRESENT="1")
            subprocess.run([bash, "-eu", (path / "guard").as_posix()], check=True, env=env)
            self.assertFalse(any(" -I " in c for c in trace.read_text().splitlines()), "Repeated boot must not duplicate firewall rules")
            env["FAIL"] = "1"
            result = subprocess.run([bash, "-eu", (path / "guard").as_posix()], env=env, capture_output=True)
            self.assertNotEqual(result.returncode, 0, "Guard failure must block its required Docker dependency")


if __name__ == "__main__":
    unittest.main()
