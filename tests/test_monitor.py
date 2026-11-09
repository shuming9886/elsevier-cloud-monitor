import json
import pathlib
import sys
import tempfile
import unittest
import logging
import stat
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import monitor


SAMPLE_BODY = """Track your submission
Peer review status
Article Title
A test article
Detailed Reviewer Activity
Reviews completed: 1
Review invitations accepted: 2+
Review invitations sent: 3+
Submission status and date
Under Review
Last review activity: 25th October 2026
Manuscript Details
Journal:
Example Journal
"""


class TargetUrlTests(unittest.TestCase):
    def test_accepts_elsevier_authorhub_uuid_url(self):
        url = "https://track.authorhub.elsevier.com/?uuid=123e4567-e89b-12d3-a456-426614174000"
        self.assertEqual(monitor.validate_target_url(url), url)

    def test_rejects_wrong_host(self):
        with self.assertRaises(ValueError):
            monitor.validate_target_url(
                "https://example.com/?uuid=123e4567-e89b-12d3-a456-426614174000"
            )

    def test_rejects_invalid_uuid(self):
        with self.assertRaises(ValueError):
            monitor.validate_target_url(
                "https://track.authorhub.elsevier.com/?uuid=not-a-uuid"
            )


class ParsingTests(unittest.TestCase):
    def test_extracts_monitored_fields(self):
        result = monitor.parse_body(SAMPLE_BODY, "Under Review")
        self.assertEqual(
            result,
            {
                "reviews_completed": "1",
                "invitations_accepted": "2+",
                "invitations_sent": "3+",
                "last_activity": "25th October 2026",
                "submission_status": "Under Review",
                "status_state": "Under Review",
            },
        )

    def test_rejects_article_not_found_page(self):
        with self.assertRaises(monitor.InvalidPageError):
            monitor.parse_body("The article you’re looking for has not been found")

    def test_rejects_page_without_review_fields(self):
        with self.assertRaises(monitor.InvalidPageError):
            monitor.parse_body("Track your submission\nTemporarily unavailable")

    def test_rejects_partial_observation(self):
        partial = SAMPLE_BODY.replace("Review invitations accepted: 2+\n", "")
        with self.assertRaises(monitor.InvalidPageError):
            monitor.parse_body(partial, "Under Review")


class StateTests(unittest.TestCase):
    def test_first_observation_creates_baseline_without_changes(self):
        current = monitor.parse_body(SAMPLE_BODY, "Under Review")
        self.assertEqual(monitor.compare_states({}, current), {})

    def test_reports_only_changed_fields(self):
        previous = monitor.parse_body(SAMPLE_BODY, "Under Review")
        current = dict(previous, reviews_completed="2", last_activity="1st November 2026")
        self.assertEqual(
            monitor.compare_states(previous, current),
            {
                "reviews_completed": ("1", "2"),
                "last_activity": ("25th October 2026", "1st November 2026"),
            },
        )

    def test_state_round_trip_is_atomic_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "state.json"
            state = {"reviews_completed": "1", "last_check": "now"}
            monitor.save_state(path, state)
            self.assertEqual(monitor.load_state(path), state)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), state)
            self.assertFalse(path.with_suffix(".json.tmp").exists())


class RuntimeSafetyTests(unittest.TestCase):
    def test_log_file_is_created_with_private_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "monitor.log"
            logger = monitor.build_logger(path)
            try:
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
            finally:
                for handler in list(logger.handlers):
                    handler.close()
                logger.handlers.clear()

    def test_browser_sandbox_is_not_disabled(self):
        source = (ROOT / "scripts" / "monitor.py").read_text(encoding="utf-8")
        self.assertNotIn("--no-sandbox", source)
        self.assertNotIn("--disable-setuid-sandbox", source)

    def test_dead_reused_browser_is_fatal_for_daemon_restart(self):
        browser = mock.Mock()
        browser.is_connected.return_value = False
        logger = logging.getLogger("dead-browser-test")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(monitor.BrowserUnavailableError):
                monitor.check_once("https://example.invalid", pathlib.Path(directory) / "state.json", logger, browser=browser)


if __name__ == "__main__":
    unittest.main()
