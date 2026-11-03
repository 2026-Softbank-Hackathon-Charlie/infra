#!/usr/bin/env python3
"""Negative probes must not mistake auth/TLS/service outages for isolation."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'examples/cnpg-poc/files/run.sh'


class ProbeTests(unittest.TestCase):
    def test_sql_assertion_failures_are_errors_not_successful_psql_quits(self):
        # Regression: psql \quit takes no exit status; '\quit 1' returns success.
        # These scripts run with ON_ERROR_STOP, so the false branch must issue SQL
        # that fails. Actual SQL/role behavior still requires the approved DB trial.
        for name in ('migrate.sql', 'verify.sql'):
            text = (SCRIPT.parent / name).read_text()
            self.assertIn('\\set ON_ERROR_STOP on', text)
            self.assertNotRegex(text, r'\\(?:quit|q)\b')
            branches = re.findall(r'\\else\s+(.*?)\\endif', text, re.S)
            self.assertTrue(branches)
            for branch in branches:
                self.assertIn('SELECT 1 / 0 AS poc_assertion_failed;', branch)

    def execute(self, mode='verify', scenario='normal', username='svc_a_runtime', service='a'):
        with tempfile.TemporaryDirectory(prefix='cnpg-job-test-') as directory:
            path = Path(directory)
            (path / 'username').write_text(username)
            (path / 'password').write_text('not-a-real-credential')
            fake = path / 'psql'
            fake.write_text('''#!/bin/sh
set -eu
printf 'psql\n' >> "$TEST_CALL_LOG"
case "$*" in
  *--file=*)
    case "$*:$TEST_SCENARIO" in *db-a-recovery.sql:apply-failure) exit 3;; esac
    exit 0;;
esac
if [ "$POC_MODE" = network-denied ]; then
    case "$TEST_SCENARIO" in
      network-open) exit 0;;
      auth-failure) echo 'password authentication failed' >&2; exit 2;;
      *) echo 'timeout expired' >&2; exit 2;;
    esac
fi
if [ "$PGDATABASE" = db_b ]; then
    case "$TEST_SCENARIO" in
      cross-open) exit 0;;
      auth-failure) echo 'password authentication failed' >&2; exit 2;;
      tls-failure) echo 'certificate verify failed' >&2; exit 2;;
      *) echo 'FATAL: pg_hba.conf rejects connection' >&2; exit 2;;
    esac
fi
case "$TEST_SCENARIO" in
  tls-open) exit 0;;
  tls-unrelated) echo 'Connection refused' >&2; exit 2;;
  *) echo 'server certificate does not match host name' >&2; exit 2;;
esac
''')
            fake.chmod(0o700)
            dump = path / 'pg_dump'
            dump.write_text('''#!/bin/sh
set -eu
printf 'dump\n' >> "$TEST_CALL_LOG"
[ "$PGHOST" = postgres-poc-restore-rw.charlie-db.svc.cluster.local ] || exit 1
[ "$PGSSLROOTCERT" = /restore-ca/ca.crt ] || exit 1
[ "$TEST_SCENARIO" != dump-failure ] || exit 17
for arg in "$@"; do case "$arg" in --file=*) printf 'SELECT 1;\n' > "${arg#--file=}";; esac; done
''')
            dump.chmod(0o700)
            getent = path / 'getent'
            getent.write_text('#!/bin/sh\necho "127.0.0.1 STREAM synthetic"\n')
            getent.chmod(0o700)
            env = dict(os.environ, PATH=str(path) + os.pathsep + os.environ['PATH'],
                       POC_MODE=mode, POC_SERVICE=service, PGDATABASE='db_' + service,
                       PGHOST='postgres-poc-rw.charlie-db.svc.cluster.local',
                       PGSSLMODE='verify-full', POC_CREDENTIALS_DIR=str(path),
                       POC_TMP_DIR=str(path), TEST_SCENARIO=scenario,
                       POC_MARKER_LABEL='synthetic-marker',
                       POC_RESTORE_HOST='postgres-poc-restore-rw.charlie-db.svc.cluster.local',
                       POC_RESTORE_CA='/restore-ca/ca.crt', TEST_CALL_LOG=str(path / 'calls'))
            result = subprocess.run(['/bin/sh', str(SCRIPT)], env=env,
                                    capture_output=True, text=True, timeout=10)
            self.assertNotIn('not-a-real-credential', result.stdout + result.stderr)
            self.assertFalse((path / 'negative-check.stderr').exists())
            self.assertFalse((path / 'db-a-recovery.sql').exists())
            result.calls = (path / 'calls').read_text().splitlines() if (path / 'calls').exists() else []
            return result

    def test_expected_access_and_tls_denials_pass(self):
        self.assertEqual(self.execute().returncode, 0)

    def test_cross_service_success_fails(self):
        self.assertNotEqual(self.execute(scenario='cross-open').returncode, 0)

    def test_other_auth_or_tls_errors_do_not_prove_db_isolation(self):
        for scenario in ('auth-failure', 'tls-failure'):
            with self.subTest(scenario=scenario):
                self.assertNotEqual(self.execute(scenario=scenario).returncode, 0)

    def test_tls_mismatch_must_be_the_observed_failure(self):
        for scenario in ('tls-open', 'tls-unrelated'):
            with self.subTest(scenario=scenario):
                self.assertNotEqual(self.execute(scenario=scenario).returncode, 0)

    def test_unlabelled_pod_must_timeout(self):
        self.assertEqual(self.execute(mode='network-denied').returncode, 0)
        for scenario in ('network-open', 'auth-failure'):
            with self.subTest(scenario=scenario):
                self.assertNotEqual(self.execute(mode='network-denied', scenario=scenario).returncode, 0)

    def test_wrong_credential_identity_is_rejected(self):
        self.assertNotEqual(self.execute(username='svc_b_runtime').returncode, 0)

    def test_migration_uses_owner_identity(self):
        self.assertEqual(self.execute(mode='migrate', username='svc_a_owner').returncode, 0)
        self.assertNotEqual(self.execute(mode='migrate').returncode, 0)

    def test_dump_must_complete_before_source_recovery(self):
        result = self.execute(mode='recover-a', username='svc_a_owner')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.calls, ['dump', 'psql', 'psql'])
        failed = self.execute(mode='recover-a', username='svc_a_owner', scenario='dump-failure')
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(failed.calls, ['dump'])

    def test_failed_source_restore_never_reports_success_or_reseeds(self):
        result = self.execute(mode='recover-a', username='svc_a_owner', scenario='apply-failure')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.calls, ['dump', 'psql'])
        self.assertNotIn('recovery-complete', result.stdout)

    def test_recovery_is_limited_to_a(self):
        result = self.execute(mode='recover-a', username='svc_b_owner', service='b')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.calls, [])

    def test_marker_and_fingerprint_use_runtime_identity(self):
        for mode in ('marker', 'fingerprint'):
            with self.subTest(mode=mode):
                result = self.execute(mode=mode)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.calls, ['psql'])


if __name__ == '__main__':
    unittest.main()
