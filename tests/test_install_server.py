import pathlib
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import install_server


VALID_ENV = """TARGET_URL=https://track.authorhub.elsevier.com/?uuid=123e4567-e89b-12d3-a456-426614174000
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=author@example.com
SMTP_PASS=secret-value
TO_EMAILS=author@example.com,coauthor@example.com
CHECK_INTERVAL=1800
"""


class EnvironmentTests(unittest.TestCase):
    def test_reads_required_values_without_printing_or_expanding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "monitor.env"
            path.write_text(VALID_ENV, encoding="utf-8")
            path.chmod(0o600)
            values = install_server.read_environment(path)
            self.assertEqual(values["SMTP_PASS"], "secret-value")
            self.assertEqual(values["CHECK_INTERVAL"], "1800")

    def test_rejects_missing_smtp_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "monitor.env"
            path.write_text(VALID_ENV.replace("SMTP_PASS=secret-value\n", ""), encoding="utf-8")
            path.chmod(0o600)
            with self.assertRaises(ValueError):
                install_server.read_environment(path)

    def test_rejects_too_frequent_polling(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "monitor.env"
            path.write_text(VALID_ENV.replace("CHECK_INTERVAL=1800", "CHECK_INTERVAL=60"), encoding="utf-8")
            path.chmod(0o600)
            with self.assertRaises(ValueError):
                install_server.read_environment(path)

    def test_runtime_environment_adds_managed_paths(self):
        values = {"TARGET_URL": "x", "SMTP_PASS": "y"}
        result = install_server.runtime_environment(values, "/custom/chrome")
        self.assertEqual(result["STATE_FILE"], "/var/lib/elsevier-monitor/state.json")
        self.assertEqual(result["LOG_FILE"], "/var/log/elsevier-monitor/monitor.log")
        self.assertEqual(result["PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"], "/custom/chrome")

    def test_rejects_invalid_or_duplicate_keys(self):
        for extra in ("BAD-KEY=value\n", "SMTP_HOST=again\n"):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as directory:
                path = pathlib.Path(directory) / "monitor.env"
                path.write_text(VALID_ENV + extra, encoding="utf-8")
                path.chmod(0o600)
                with self.assertRaises(ValueError):
                    install_server.read_environment(path)

    def test_rejects_symlink_and_insecure_source_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "real.env"
            target.write_text(VALID_ENV, encoding="utf-8")
            target.chmod(0o644)
            with self.assertRaises(ValueError):
                install_server.read_environment(target)
            target.chmod(0o600)
            link = pathlib.Path(directory) / "link.env"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                install_server.read_environment(link)

    def test_rejects_invalid_smtp_ports(self):
        for port in ("abc", "25"):
            with self.subTest(port=port), tempfile.TemporaryDirectory() as directory:
                path = pathlib.Path(directory) / "monitor.env"
                path.write_text(VALID_ENV.replace("SMTP_PORT=587", f"SMTP_PORT={port}"), encoding="utf-8")
                path.chmod(0o600)
                with self.assertRaises(ValueError):
                    install_server.read_environment(path)


class InstallationSafetyTests(unittest.TestCase):
    def test_supported_server_requires_exact_os_and_systemd_pid_one(self):
        with mock.patch.object(install_server.os, "geteuid", return_value=0), \
             mock.patch.object(install_server.sys, "platform", "linux"), \
             mock.patch.object(install_server.platform, "machine", return_value="x86_64"), \
             mock.patch.object(install_server.shutil, "which", return_value="/bin/tool"), \
             mock.patch.object(install_server, "read_os_release", return_value={"ID": "ubuntu", "VERSION_ID": "20.04"}), \
             mock.patch.object(install_server, "is_systemd_pid_one", return_value=True):
            with self.assertRaises(RuntimeError):
                install_server.require_supported_server()

        with mock.patch.object(install_server.os, "geteuid", return_value=0), \
             mock.patch.object(install_server.sys, "platform", "linux"), \
             mock.patch.object(install_server.platform, "machine", return_value="x86_64"), \
             mock.patch.object(install_server.shutil, "which", return_value="/bin/tool"), \
             mock.patch.object(install_server, "read_os_release", return_value={"ID": "debian", "VERSION_ID": "12"}), \
             mock.patch.object(install_server, "is_systemd_pid_one", return_value=False):
            with self.assertRaises(RuntimeError):
                install_server.require_supported_server()

    def test_chrome_install_uses_signed_repository_and_verifies_fingerprint(self):
        calls = []
        def record(*args, **kwargs):
            calls.append(args)
        with mock.patch.object(install_server.shutil, "which", side_effect=[None, None, "/usr/bin/google-chrome"]), \
             mock.patch.object(install_server, "run", side_effect=record), \
             mock.patch.object(install_server, "verify_google_key_fingerprint") as verify:
            self.assertEqual(install_server.install_chrome(), "/usr/bin/google-chrome")
        verify.assert_called_once()
        flattened = [" ".join(call) for call in calls]
        self.assertTrue(any("google-chrome.list" in call for call in flattened))
        self.assertTrue(any("google-chrome-stable" in call for call in flattened))
        self.assertFalse(any("stable_current_amd64.deb" in call for call in flattened))

    def test_service_and_rotation_templates_are_unprivileged_and_reopen_safe(self):
        service = (ROOT / "templates" / "elsevier-monitor.service").read_text(encoding="utf-8")
        rotation = (ROOT / "templates" / "elsevier-monitor.logrotate").read_text(encoding="utf-8")
        self.assertIn("User=elsevier-monitor", service)
        self.assertIn("Group=elsevier-monitor", service)
        self.assertIn("StartLimitBurst=", service)
        self.assertNotIn("User=root", service)
        self.assertIn("copytruncate", rotation)
        self.assertIn("create 640 elsevier-monitor elsevier-monitor", rotation)

    def test_docs_do_not_source_environment_as_shell(self):
        docs = "\n".join((ROOT / name).read_text(encoding="utf-8") for name in ("SKILL.md", "references/troubleshooting.md"))
        self.assertNotIn(". /etc/elsevier-monitor.env", docs)
        self.assertNotIn("set -a", docs)
        self.assertIn("--exec", docs)
        self.assertIn("mktemp -d", docs)

    def test_installer_stops_before_baseline_and_restarts_in_finally(self):
        source = (ROOT / "scripts" / "install_server.py").read_text(encoding="utf-8")
        stop = source.index('"systemctl", "stop", SERVICE_NAME')
        baseline = source.index("抓取页面并建立初始基线")
        self.assertLess(stop, baseline)
        self.assertIn("finally:", source)
        self.assertIn('"systemctl", "restart", SERVICE_NAME', source)


if __name__ == "__main__":
    unittest.main()
