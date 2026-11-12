"""Read-only Google digest regressions; no live Google calls or real credentials."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

import external_digest as e
import personal_pipeline as pipeline
from personal_rules import dedup_keys

NOW = datetime(2026, 11, 12, 14, tzinfo=timezone.utc)
CLIENT = {"installed": {"client_id": "fake-client-123.apps.googleusercontent.com", "client_secret": "fake-secret",
                        "token_uri": "https://untrusted.invalid/token"}}
FOLDER = "fake_folder_123"
SHEET = "fake_sheet_123"


class ExternalDigestTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        self.digest = e.ExternalDigest(self.root)
        self.digest.configure({"folder": FOLDER, "client": CLIENT})
        value = e.read(self.digest.root/"credentials.json")
        value["refresh_token"] = "fake-refresh"
        e.write(self.digest.root/"credentials.json", value)

    def api(self, url, token=None, form=None):
        self.calls.append((url, token, form))
        if form:
            self.assertEqual(url, "https://oauth2.googleapis.com/token")
            return {"access_token": "fake-access"}
        if "drive/v3/files?" in url:
            return {"files": [{"id": SHEET, "name": "Jobs -- 2026-11-12", "modifiedTime": NOW.isoformat()}]}
        if "/values/" in url:
            return {"values": self.values}
        return {"sheets": [{"properties": {"title": "Jobs -- 2026-11-12", "sheetType": "GRID",
                                          "gridProperties": {"columnCount": 11, "rowCount": 1000}}}]}

    def test_reads_today_sheet_and_dedup_keys_preserve_job_url(self):
        self.calls = []
        self.values = [["Title", "Company", "Location", "Apply URL"],
                       ["DevOps Engineer", "Example", "Birmingham, UK", "https://www.linkedin.com/jobs/view/1234567890/"]]
        with patch.object(e, "google", side_effect=self.api):
            jobs, report = self.digest.sync(NOW)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["as_of"], "2026-11-12")
        self.assertEqual(len(jobs), 1)
        self.assertTrue(set(pipeline.key_set(jobs[0])) & pipeline.key_set({"id": "1234567890"}))
        queries = [parse_qs(urlsplit(url).query) for url, _, _ in self.calls if "drive/v3/files?" in url]
        self.assertIn("name contains '2026-11-12'", queries[0]["q"][0])
        area = next(unquote(urlsplit(url).path) for url, _, _ in self.calls if "/values/" in url)
        self.assertIn("!A1:K500", area)
        self.assertFalse(any(form and form.get("grant_type") != "refresh_token" for _, _, form in self.calls))
        self.assertNotIn("fake-refresh", json.dumps(self.digest.status(NOW)))
        self.assertEqual((self.digest.root/"credentials.json").stat().st_mode & 0o777, 0o600)

    def test_recognised_header_only_is_confirmed_zero_but_blank_or_unknown_sheet_is_not(self):
        for values, expected in (([["Title", "Company", "Location", "Apply URL"]], True),
                                 ([], False), ([["Notes", "Status"]], False),
                                 ([["Title", "Company", "Location"], ["DevOps", "", ""]], False)):
            self.calls, self.values = [], values
            with self.subTest(values=values), patch.object(e, "google", side_effect=self.api):
                if expected:
                    rows, report = self.digest.sync(NOW)
                    self.assertEqual(rows, [])
                    self.assertEqual(report["status"], "ok")
                else:
                    with self.assertRaises(e.DigestError):
                        self.digest.sync(NOW)
                    self.assertEqual(self.digest.status(NOW)["status"], "incomplete")

    def test_missing_today_or_access_failure_does_not_relabel_old_snapshot(self):
        old = {"as_of": "2026-11-02", "jobs": [{"id": "1234567890"}]}
        e.write(self.digest.root/"index.json", old)
        for result in ({}, {"files": [{"id": SHEET, "name": "Jobs -- 2026-11-11"}]}, {"incompleteSearch": True}):
            with self.subTest(result=result), patch.object(self.digest, "_access", return_value="fake"), patch.object(e, "google", return_value=result):
                with self.assertRaises(e.DigestError):
                    self.digest.sync(NOW)
                self.assertEqual(e.read(self.digest.root/"index.json"), old)
                self.assertEqual(self.digest.status(NOW)["status"], "incomplete")

    def test_oauth_uses_readonly_scopes_pkce_single_use_state_and_fixed_token_endpoint(self):
        auth = self.digest.connect(8787)
        query = parse_qs(urlsplit(auth["url"]).query)
        self.assertEqual(set(query["scope"][0].split()), set(e.SCOPES))
        self.assertTrue(all(scope.endswith("readonly") for scope in e.SCOPES))
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["redirect_uri"], ["http://127.0.0.1:8787/api/external-digest/callback"])
        with patch.object(e, "google") as api:
            with self.assertRaises(e.DigestError):
                self.digest.callback({"state": ["invalid"], "code": ["fake"]})
            api.assert_not_called()
        response = {"refresh_token": "new-fake-refresh", "scope": " ".join(e.SCOPES)}
        with patch.object(e, "google", return_value=response) as api:
            self.digest.callback({"state": query["state"], "code": ["fake-code"]})
            args, kwargs = api.call_args
            self.assertEqual(args[0], "https://oauth2.googleapis.com/token")
            self.assertIn("code_verifier", kwargs["form"])
            self.assertNotIn("fake-code", json.dumps(self.digest.status(NOW)))
            with self.assertRaises(e.DigestError):
                self.digest.callback({"state": query["state"], "code": ["fake-code"]})
            self.assertEqual(api.call_count, 1)

    def test_partial_consent_and_expired_state_do_not_connect(self):
        for response in ({"refresh_token": "fake", "scope": e.SCOPES[0]}, {"scope": " ".join(e.SCOPES)}):
            auth = self.digest.connect(8788)
            state = parse_qs(urlsplit(auth["url"]).query)["state"]
            with patch.object(e, "google", return_value=response):
                with self.assertRaises(e.DigestError):
                    self.digest.callback({"state": state, "code": ["fake"]})
        self.digest.connect(8788)
        pending = e.read(self.digest.root/"pending.json")
        pending["created"] -= 700
        e.write(self.digest.root/"pending.json", pending)
        with patch.object(e, "google") as api:
            with self.assertRaises(e.DigestError):
                self.digest.callback({"state": [pending["state"]], "code": ["fake"]})
            api.assert_not_called()

    def test_configured_google_source_supersedes_stale_baked_snapshot_and_failure_stays_incomplete(self):
        stale = self.root/"stale.json"
        stale.write_text(json.dumps({"as_of": "2000-01-01", "jobs": [{"id": "old"}]}))
        report = {"status": "ok", "as_of": "2026-11-12", "count": 0}
        config = {"crossDigestPaths": [str(stale)]}
        with patch.object(e.ExternalDigest, "sync", return_value=([], report)) as sync:
            rows, reports = pipeline.index_jobs(config, NOW, self.root)
            self.assertEqual(reports, [report])
            sync.assert_called_once_with(NOW)
        with patch.object(e.ExternalDigest, "sync", side_effect=e.DigestError("No same-day sheet")):
            rows, reports = pipeline.index_jobs(config, NOW, self.root)
        self.assertEqual(rows, [])
        self.assertEqual(reports[0]["status"], "incomplete")
        self.assertEqual(reports[0]["error"], "No same-day sheet")

    def test_stale_success_is_not_current_on_the_next_uk_day(self):
        e.write(self.digest.root/"status.json", {"status": "ok", "as_of": "2026-11-11", "count": 10})
        status = self.digest.status(NOW)
        self.assertEqual(status["status"], "incomplete")
        self.assertIsNone(status["count"])

    def test_readonly_configuration_ignores_uploaded_endpoints_and_rejects_invalid_folders(self):
        self.assertNotIn("token_uri", e.read(self.digest.root/"credentials.json"))
        with self.assertRaises(e.DigestError):
            self.digest.configure({"folder": "x' or trashed=true"})
        with self.assertRaises(e.DigestError):
            self.digest.configure({"folder": FOLDER, "client": {"web": CLIENT["installed"]}})


class ExternalDigestAPITests(unittest.TestCase):
    def setUp(self):
        import dashboard_server as d
        import threading
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        self.app = d.DashboardApp(self.root, self.root/"data")
        self.server = d.make_server(self.app, port=0)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def request(self, method, path, body=None, origin=None):
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, json.loads(raw) if raw else None

    def test_setup_status_and_overview_do_not_expose_credentials(self):
        status, value = self.request("POST", "/api/external-digest/settings", {"folder": FOLDER, "client": CLIENT})
        self.assertEqual(status, 200)
        self.assertTrue(value["clientUploaded"])
        for route in ("/api/external-digest", "/api/overview"):
            status, value = self.request("GET", route)
            self.assertEqual(status, 200)
            for secret in ("fake-secret", "fake-refresh", "client_secret", "refresh_token"):
                self.assertNotIn(secret, json.dumps(value))
        status, auth = self.request("POST", "/api/external-digest/connect", {})
        self.assertEqual(status, 200)
        query = parse_qs(urlsplit(auth["url"]).query)
        self.assertEqual(query["redirect_uri"], [f"http://127.0.0.1:{self.server.server_port}/api/external-digest/callback"])

    def test_callback_auth_code_is_not_logged_and_cross_origin_setup_is_rejected(self):
        import dashboard_server as d
        with patch.object(d.LOG, "info") as info:
            status, _ = self.request("GET", "/api/external-digest/callback?state=wrong&code=fake-sensitive-code")
            self.assertEqual(status, 422)
            self.assertNotIn("fake-sensitive-code", str(info.call_args_list))
        status, _ = self.request("POST", "/api/external-digest/settings", {"folder": FOLDER}, origin="https://elsewhere.invalid")
        self.assertEqual(status, 403)

    def test_active_search_blocks_connection_reconfiguration(self):
        import dashboard_server as d
        from unittest.mock import PropertyMock
        with patch.object(d.Runner, "active", new_callable=PropertyMock, return_value={"state":"running"}):
            status, value = self.request("POST", "/api/external-digest/settings", {"folder": FOLDER})
        self.assertEqual(status, 409)
        self.assertIn("current search", value["error"])
