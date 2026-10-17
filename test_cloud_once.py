"""Offline verification: no credential values or live API calls."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cloud_once as probe


class CloudOnceTests(unittest.TestCase):
    def setUp(self):
        self.env = {key: "synthetic-test-value" for key in probe.SECRET_NAMES}
        self.session = MagicMock()
        self.session.__enter__.return_value = self.session
        self.factory = MagicMock(return_value=self.session)
        self.response = self.session.get.return_value
        self.response.status_code = 200
        self.response.json.return_value = {"data": {"username": probe.EXPECTED_HANDLE, "id": "123"}}

    def run_probe(self):
        return probe.auth_probe(self.env, self.factory)

    def test_success_is_auth_only_one_read_and_transport_constrained(self):
        outcome = self.run_probe()
        self.assertEqual(outcome["status"], "PASS_AUTH_ONLY")
        self.assertEqual(outcome["public_posts"], 0)
        self.assertEqual(outcome["api_requests"], 1)
        self.assertFalse(self.session.trust_env)
        self.session.get.assert_called_once_with(
            probe.ENDPOINT,
            allow_redirects=False, timeout=(10, 30), verify=True)
        adapter = self.session.mount.call_args.args[1]
        self.assertEqual(adapter.max_retries.total, 0)
        self.session.post.assert_not_called()
        self.session.delete.assert_not_called()

    def test_native_signing_session_reads_environment(self):
        with patch.dict(probe.os.environ, self.env, clear=True), patch.object(probe, "OAuth1Session") as factory:
            probe.create_session()
            factory.assert_called_once()
            self.assertEqual(factory.call_args.args, (os.environ["X_CONSUMER_KEY"],))
            self.assertEqual(factory.call_args.kwargs, {
                "client_secret": os.environ["X_CONSUMER_SECRET"],
                "resource_owner_key": os.environ["X_ACCESS_TOKEN"],
                "resource_owner_secret": os.environ["X_ACCESS_TOKEN_SECRET"],
            })

    def test_missing_credentials_no_request(self):
        for key in probe.SECRET_NAMES:
            with self.subTest(key=key):
                env = dict(self.env)
                env[key] = "  "
                self.assertEqual(probe.auth_probe(env, self.factory)["status"], "HOLD_CREDENTIALS_MISSING")
        self.factory.assert_not_called()

    def test_rerun_and_invalid_attempt_no_request(self):
        for attempt in ("2", "0", "01", "", "abc"):
            self.env["GITHUB_RUN_ATTEMPT"] = attempt
            self.assertEqual(self.run_probe()["status"], "HOLD_RERUN")
        self.factory.assert_not_called()

    def test_wrong_account_does_not_expose_identity(self):
        self.response.json.return_value = {"data": {"username": "private-other-handle", "id": "123"}}
        outcome = self.run_probe()
        self.assertEqual(outcome["status"], "HOLD_ACCOUNT_MISMATCH")
        self.assertNotIn("private-other-handle", json.dumps(outcome))

    def test_error_only_allowlisted_integer_codes(self):
        self.response.status_code = 401
        self.response.json.return_value = {"errors": [{"code": 89, "message": "secret-text"}, {"code": 987654321}, {"code": "32"}]}
        outcome = self.run_probe()
        self.assertEqual(outcome["known_error_codes"], [89])
        self.assertNotIn("secret-text", json.dumps(outcome))
        self.assertNotIn("987654321", json.dumps(outcome))

    def test_200_with_error_is_not_success(self):
        self.response.json.return_value["errors"] = [{"code": 32}]
        self.assertEqual(self.run_probe()["status"], "HOLD_API_RESPONSE")

    def test_malformed_bodies(self):
        for body in (None, [], "private text", {}, {"data": {"username": probe.EXPECTED_HANDLE, "id": "x"}}):
            self.response.json.return_value = body
            self.assertEqual(self.run_probe()["status"], "HOLD_INVALID_RESPONSE")

    def test_bad_json_and_http_status(self):
        self.response.json.side_effect = ValueError("private response")
        self.assertEqual(self.run_probe()["status"], "HOLD_INVALID_RESPONSE")
        self.response.status_code = "private response"
        outcome = self.run_probe()
        self.assertEqual(outcome["status"], "HOLD_INVALID_RESPONSE")
        self.assertIsNone(outcome["http_status"])

    def test_redirect_not_followed_or_successful(self):
        self.response.status_code = 302
        self.assertEqual(self.run_probe()["status"], "HOLD_API_RESPONSE")
        self.session.get.assert_called_once()

    def test_network_exception_sanitized_without_retry(self):
        self.session.get.side_effect = RuntimeError("OAuth private secret")
        outcome = self.run_probe()
        self.assertEqual(outcome["status"], "HOLD_REQUEST_FAILED")
        self.assertNotIn("private", json.dumps(outcome))
        self.session.get.assert_called_once()

    def test_default_cli_is_offline(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "result.json"
            with patch.object(probe, "auth_probe") as network, patch("builtins.print"):
                self.assertEqual(probe.main(["--output", str(output)]), 0)
            network.assert_not_called()
            self.assertEqual(json.loads(output.read_text())["status"], "DRY_RUN_NO_NETWORK")


if __name__ == "__main__":
    unittest.main()
