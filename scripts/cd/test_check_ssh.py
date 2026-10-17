"""Offline tests: fake SSH tools, never connect to a real server."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("check-ssh.sh").resolve()


class ConnectionCheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="resume-ssh-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        bindir = self.root / "bin"
        bindir.mkdir()
        tools = {
            "ssh-keygen": '''#!/bin/bash
if [[ "$1" == "-F" && "${MOCK_KNOWN_HOST_MISSING:-}" == 1 ]]; then exit 1; fi
if [[ "$1" == "-y" && "${MOCK_INVALID_KEY:-}" == 1 ]]; then exit 1; fi
exit 0
''',
            "ssh": '''#!/bin/bash
set -eu
[[ -z "${CD_SSH_PRIVATE_KEY:-}" ]] || exit 80
[[ -z "${CD_SSH_KNOWN_HOSTS:-}" ]] || exit 81
printf '%s\\n' "$@" > "$MOCK_ARGS"
cat > "$MOCK_STDIN"
exit "${MOCK_SSH_EXIT:-0}"
''',
        }
        for name, contents in tools.items():
            path = bindir / name
            path.write_text(contents)
            path.chmod(0o700)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("CD_SSH_")}
        self.env.update(
            PATH=f"{bindir}:{self.env['PATH']}",
            TMPDIR=str(self.root),
            MOCK_ARGS=str(self.root / "args"),
            MOCK_STDIN=str(self.root / "stdin"),
        )
        self.credentials = dict(
            CD_SSH_HOST="example.test",
            CD_SSH_PORT="25909",
            CD_SSH_USER="probe",
            CD_SSH_PRIVATE_KEY="TEST-PRIVATE-KEY",
            CD_SSH_KNOWN_HOSTS="TEST-HOST-KEY",
        )

    def run_probe(self, credentials, expected_code=0, **extra):
        result = subprocess.run(
            ["bash", str(SCRIPT)], env=self.env | credentials | extra,
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, expected_code, result.stderr)
        self.assertNotIn("TEST-PRIVATE-KEY", result.stdout + result.stderr)
        self.assertEqual(list(self.root.glob("tmp.*")), [], "credentials not cleaned up")
        return result

    def test_missing_secrets_fail_before_ssh(self):
        for name in self.credentials:
            with self.subTest(secret=name):
                values = self.credentials.copy()
                del values[name]
                result = self.run_probe(values, 1)
                self.assertIn(f"missing {name}", result.stderr)
                self.assertFalse((self.root / "args").exists())

    def test_invalid_parameters_fail_before_ssh(self):
        for field, value in [
            ("CD_SSH_HOST", "-oProxyCommand=bad"),
            ("CD_SSH_HOST", "host;id"),
            ("CD_SSH_USER", "root;id"),
            ("CD_SSH_PORT", "0"),
            ("CD_SSH_PORT", "65536"),
            ("CD_SSH_PORT", "1+2"),
        ]:
            with self.subTest(field=field, value=value):
                self.run_probe(self.credentials | {field: value}, 1)
                self.assertFalse((self.root / "args").exists())

    def test_invalid_key_is_rejected(self):
        self.run_probe(self.credentials, 1, MOCK_INVALID_KEY="1")
        self.assertFalse((self.root / "args").exists())

    def test_unpinned_host_is_rejected(self):
        self.run_probe(self.credentials, 1, MOCK_KNOWN_HOST_MISSING="1")
        self.assertFalse((self.root / "args").exists())

    def test_success_uses_strict_options_and_readonly_script(self):
        self.run_probe(self.credentials)
        args = (self.root / "args").read_text().splitlines()
        for option in [
            "StrictHostKeyChecking=yes", "IdentitiesOnly=yes", "BatchMode=yes",
            "ForwardAgent=no", "IdentityAgent=none", "ClearAllForwardings=yes",
            "GlobalKnownHostsFile=/dev/null", "UpdateHostKeys=no",
        ]:
            self.assertIn(option, args)
        self.assertEqual(args[:2], ["-F", "/dev/null"])
        self.assertEqual(args[-2:], ["example.test", "bash -se"])
        self.assertEqual(
            (self.root / "stdin").read_text(),
            SCRIPT.with_name("remote-probe.sh").read_text(),
        )

    def test_ssh_failure_propagates_and_cleans_credentials(self):
        self.run_probe(self.credentials, 255, MOCK_SSH_EXIT="255")

    def test_leading_zero_port_is_decimal(self):
        self.run_probe(self.credentials | {"CD_SSH_PORT": "00022"})
        args = (self.root / "args").read_text().splitlines()
        self.assertEqual(args[args.index("-p") + 1], "22")


if __name__ == "__main__":
    unittest.main()
