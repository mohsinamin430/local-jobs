"""Read-only Google Sheets digest sync with local OAuth credentials."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from zoneinfo import ZoneInfo
from personal_rules import canonical_url

SCOPES = ("https://www.googleapis.com/auth/drive.metadata.readonly",
          "https://www.googleapis.com/auth/spreadsheets.readonly")
ID = re.compile(r"[A-Za-z0-9_-]{8,160}\Z")
UTC = timezone.utc


class DigestError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        return None


def read(path, default=None):
    if path.is_symlink():
        raise DigestError("Digest data cannot use symbolic links.")
    if not path.exists():
        return default
    if path.stat().st_size > 4_000_000:
        raise DigestError("Digest data exceeds the supported size.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as error:
        raise DigestError("The saved digest data could not be read.") from error


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise DigestError("Digest data cannot use symbolic links.")
    temporary = path.with_name(path.name + "." + secrets.token_hex(6) + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as file:
            os.chmod(temporary, 0o600)
            json.dump(value, file, ensure_ascii=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def google(url, token=None, form=None):
    host = urlsplit(url).hostname
    if urlsplit(url).scheme != "https" or host not in {"oauth2.googleapis.com", "www.googleapis.com", "sheets.googleapis.com"}:
        raise DigestError("Unsupported Google endpoint.")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if form is not None:
        data = urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        with build_opener(NoRedirect).open(Request(url, data=data, headers=headers), timeout=15) as response:
            raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise DigestError("Google returned more digest data than can be checked safely.")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise DigestError("Google returned an invalid response.")
        return value
    except HTTPError as error:
        if error.code in (400, 401):
            message = "Google authorisation expired or was declined. Reconnect Google in External digest settings."
        elif error.code == 403:
            message = "Google denied read access. Enable the Drive and Sheets APIs and check the account can open the digest folder."
        elif error.code == 429:
            message = "Google rate-limited the digest refresh. The external comparison remains incomplete; try again later."
        else:
            message = f"Google digest refresh returned HTTP {error.code}."
        raise DigestError(message) from None
    except (URLError, TimeoutError, OSError, ValueError) as error:
        if isinstance(error, DigestError):
            raise
        raise DigestError("Google digest refresh failed. Check the connection and try again.") from None


def normalise_rows(values):
    """Recognise existing digest headers; unknown/malformed rows never become a zero."""
    if not isinstance(values, list) or not values or not isinstance(values[0], list):
        raise DigestError("The digest has no recognised header row; it was not treated as an empty result.")
    headers = [re.sub(r"[^a-z0-9]", "", str(cell).lower()) for cell in values[0]]
    aliases = {
        "id": ("id", "jobid", "linkedinjobid"),
        "title": ("title", "jobtitle", "role", "position"),
        "company": ("company", "employer", "companyname"),
        "location": ("location", "joblocation"),
        "apply_url": ("applyurl", "directapplyurl", "canonicalurl", "preferredapplyurl", "applicationurl"),
        "url": ("url", "joburl", "linkedinurl", "linkedinjoburl"),
    }
    columns = {key: next((headers.index(alias) for alias in names if alias in headers), None)
               for key, names in aliases.items()}
    if not (columns["id"] is not None or columns["url"] is not None or
            columns["apply_url"] is not None or
            all(columns[key] is not None for key in ("title", "company", "location"))):
        raise DigestError("The digest needs a job ID, job/apply URL, or title, company and location columns.")
    rows = []
    for cells in values[1:]:
        if not isinstance(cells, list):
            raise DigestError("A digest row has an unsupported format.")
        if not any(str(cell).strip() for cell in cells):
            continue
        row = {key: str(cells[column]).strip() if column is not None and column < len(cells) else ""
               for key, column in columns.items()}
        for field in ("url", "apply_url"):
            row[field] = canonical_url(row[field])
            parsed = urlsplit(row[field])
            if parsed.hostname == "www.linkedin.com" and not row["id"]:
                match = re.fullmatch(r"/jobs/view/(\d{7,})", parsed.path)
                if match:
                    row["id"] = match.group(1)
        if not (row["id"] or row["url"] or row["apply_url"] or
                all(row[key] for key in ("title", "company", "location"))):
            raise DigestError("A populated digest row has no usable job identity; comparison is incomplete.")
        rows.append(row)
    return rows


class ExternalDigest:
    def __init__(self, data):
        self.root = Path(data) / "external-digest"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def settings(self):
        return read(self.root / "settings.json", {})

    def status(self, now=None):
        settings = self.settings()
        credentials = read(self.root / "credentials.json", {})
        saved = read(self.root / "status.json", {})
        today = (now or datetime.now(UTC)).astimezone(ZoneInfo("Europe/London")).date().isoformat()
        current = saved.get("status") == "ok" and saved.get("as_of") == today
        return {"configured": bool(settings), "connected": bool(credentials.get("refresh_token")),
                "folder": settings.get("folder", ""), "prefix": settings.get("prefix", "Jobs"),
                "tab": settings.get("tab", ""), "clientUploaded": bool(credentials.get("client_id")),
                "status": "ok" if current else "incomplete",
                "asOf": saved.get("as_of", ""), "checkedAt": saved.get("checked_at", ""),
                "count": saved.get("count") if current else None,
                "sources": saved.get("sources", []),
                "message": saved.get("error") or ("External digest index is current." if current else
                    "A current Google Sheets index has not been verified. Connect Google and refresh.")}

    def configure(self, payload):
        folder = payload.get("folder", "")
        prefix = payload.get("prefix", "Jobs")
        tab = payload.get("tab", "")
        if not all(isinstance(value, str) for value in (folder, prefix, tab)):
            raise DigestError("Enter a Google Drive folder and a digest title prefix.")
        match = re.fullmatch(r"https://drive\.google\.com/drive/(?:u/\d+/)?folders/([A-Za-z0-9_-]+)(?:\?[^#]*)?", folder.strip())
        folder = match.group(1) if match else folder.strip()
        if not ID.fullmatch(folder) or not 1 <= len(prefix.strip()) <= 80 or len(tab) > 100:
            raise DigestError("Use a Drive folder URL/ID, a title prefix up to 80 characters and an optional tab name.")
        credentials = read(self.root / "credentials.json", {})
        if payload.get("client") is not None:
            client = payload["client"]
            if not isinstance(client, dict) or not isinstance(client.get("installed"), dict):
                raise DigestError("Upload the OAuth client JSON for a Google Desktop app.")
            client = client["installed"]
            identifier, secret = client.get("client_id", ""), client.get("client_secret", "")
            if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9._-]{5,250}\.apps\.googleusercontent\.com", identifier):
                raise DigestError("The Desktop app client ID is invalid.")
            if not isinstance(secret, str) or not 1 <= len(secret) <= 300:
                raise DigestError("The Desktop app client secret is missing.")
            # Ignore uploaded token/auth URLs. Credentials are only sent to fixed Google endpoints.
            credentials = {"client_id": identifier, "client_secret": secret}
            write(self.root / "credentials.json", credentials)
        write(self.root / "settings.json", {"folder": folder, "prefix": prefix.strip(), "tab": tab.strip()})
        write(self.root / "status.json", {"status": "incomplete", "error": "Settings saved. Connect Google, then refresh the external digest."})
        return self.status()

    def connect(self, port):
        credentials = read(self.root / "credentials.json", {})
        if not credentials.get("client_id"):
            raise DigestError("Upload a Google Desktop app OAuth client JSON first.")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        redirect = f"http://127.0.0.1:{int(port)}/api/external-digest/callback"
        write(self.root / "pending.json", {"state": state, "verifier": verifier, "redirect": redirect,
                                          "client_id": credentials["client_id"], "created": time.time()})
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        return {"url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": credentials["client_id"], "redirect_uri": redirect, "response_type": "code",
            "scope": " ".join(SCOPES), "state": state, "code_challenge": challenge,
            "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent"})}

    def callback(self, query):
        pending = read(self.root / "pending.json", {})
        state = query.get("state", [""])[0]
        if not pending or not secrets.compare_digest(str(pending.get("state", "")), state) or time.time() - pending.get("created", 0) > 600:
            raise DigestError("Google connection expired or the security check failed. Start Connect Google again.")
        (self.root / "pending.json").unlink(missing_ok=True)
        if query.get("error"):
            raise DigestError("Google access was declined. The external digest comparison remains incomplete.")
        code = query.get("code", [""])[0]
        credentials = read(self.root / "credentials.json", {})
        if not code or pending.get("client_id") != credentials.get("client_id"):
            raise DigestError("The connection changed. Start Connect Google again.")
        result = google("https://oauth2.googleapis.com/token", form={
            "grant_type": "authorization_code", "client_id": credentials["client_id"],
            "client_secret": credentials["client_secret"], "code": code,
            "redirect_uri": pending["redirect"], "code_verifier": pending["verifier"]})
        if not result.get("refresh_token") or not set(SCOPES) <= set(result.get("scope", "").split()):
            raise DigestError("Google did not grant both read-only permissions with offline access. Reconnect and allow both.")
        credentials["refresh_token"] = result["refresh_token"]
        write(self.root / "credentials.json", credentials)
        return self.status()

    def _access(self):
        credentials = read(self.root / "credentials.json", {})
        if not credentials.get("refresh_token"):
            raise DigestError("Connect Google in Profile & preferences > External digest. The app needs its own read-only authorisation.")
        result = google("https://oauth2.googleapis.com/token", form={
            "grant_type": "refresh_token", "client_id": credentials["client_id"],
            "client_secret": credentials["client_secret"], "refresh_token": credentials["refresh_token"]})
        if not isinstance(result.get("access_token"), str) or not result["access_token"]:
            raise DigestError("Google did not return an access token. Reconnect Google.")
        return result["access_token"]

    def sync(self, now=None):
        now = now or datetime.now(UTC)
        today = now.astimezone(ZoneInfo("Europe/London")).date().isoformat()
        checked = now.isoformat()
        settings = self.settings()
        if not settings:
            raise DigestError("Configure an external Google Sheets digest first.")
        with (self.root / "sync.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DigestError("An external digest refresh is already running. Try again when it finishes.") from None
            try:
                token = self._access()
                # Folder/prefix are validated; escape query literals regardless.
                literal = lambda value: value.replace("\\", "\\\\").replace("'", "\\'")
                query = ("trashed = false and mimeType = 'application/vnd.google-apps.spreadsheet' and " +
                         f"'{literal(settings['folder'])}' in parents and " +
                         f"name contains '{literal(settings['prefix'])}' and name contains '{today}'")
                files, page_token = [], ""
                for _ in range(20):
                    args = {"q": query, "fields": "nextPageToken,incompleteSearch,files(id,name,modifiedTime)",
                            "pageSize": 100, "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
                    if page_token:
                        args["pageToken"] = page_token
                    response = google("https://www.googleapis.com/drive/v3/files?" + urlencode(args), token)
                    if response.get("incompleteSearch"):
                        raise DigestError("Google returned an incomplete file search; external comparison is incomplete.")
                    files.extend(response.get("files", []))
                    page_token = response.get("nextPageToken", "")
                    if not page_token:
                        break
                if page_token:
                    raise DigestError("Google file search exceeded its page limit; external comparison is incomplete.")
                pattern = re.compile(re.escape(settings["prefix"]) + r"\s*[-–—]{1,2}\s*" + re.escape(today) + r"\Z", re.I)
                files = [file for file in files if pattern.fullmatch(file.get("name", ""))]
                if not files:
                    raise DigestError(f"No '{settings['prefix']} -- {today}' Google Sheet was found in the selected folder. Missing sources are not treated as zero jobs.")
                rows, sources = [], []
                for file in files:
                    identifier = file.get("id", "")
                    if not ID.fullmatch(identifier):
                        raise DigestError("Google returned an unsupported spreadsheet ID.")
                    base = "https://sheets.googleapis.com/v4/spreadsheets/" + identifier
                    metadata = google(base + "?" + urlencode({"fields": "sheets(properties)"}), token)
                    tabs = [tab["properties"] for tab in metadata.get("sheets", []) if "properties" in tab]
                    if settings.get("tab"):
                        tabs = [tab for tab in tabs if tab.get("title") == settings["tab"]]
                    else:
                        # A single visible grid tab or exact Jobs/daily-title tab is unambiguous.
                        tabs = [tab for tab in tabs if not tab.get("hidden") and tab.get("sheetType", "GRID") == "GRID"]
                        if len(tabs) != 1:
                            tabs = [tab for tab in tabs if tab.get("title") in {settings["prefix"], file["name"]}]
                    if len(tabs) != 1:
                        raise DigestError("The digest tab is ambiguous or missing. Enter its exact name in External digest settings.")
                    title = tabs[0]["title"]
                    columns = tabs[0].get("gridProperties", {}).get("columnCount", 26)
                    if columns > 52:
                        raise DigestError("The digest exceeds 52 columns; select a compact jobs tab.")
                    columns = max(1, columns)
                    end_column = chr(64 + columns) if columns <= 26 else "A" + chr(64 + columns - 26)
                    row_count = tabs[0].get("gridProperties", {}).get("rowCount", 10000)
                    values, offset = [], 1
                    for _ in range(20):
                        end_row = min(offset + 499, row_count)
                        area = "'" + title.replace("'", "''") + f"'!A{offset}:{end_column}{end_row}"
                        result = google(base + "/values/" + quote(area, safe="") + "?" +
                                        urlencode({"valueRenderOption": "FORMATTED_VALUE"}), token)
                        part = result.get("values", [])
                        values.extend(part)
                        if len(part) < end_row - offset + 1 or end_row >= row_count:
                            break
                        offset += 500
                    else:
                        raise DigestError("The digest exceeds 10,000 rows; the external comparison is incomplete.")
                    rows.extend(normalise_rows(values))
                    sources.append({"title": file["name"], "url": f"https://docs.google.com/spreadsheets/d/{identifier}/edit",
                                    "tab": title, "modified_at": file.get("modifiedTime", "")})
                document = {"as_of": today, "checked_at": checked, "source_url": sources[0]["url"],
                            "sources": sources, "jobs": rows}
                write(self.root / "index.json", document)
                write(self.root / "status.json", {"status": "ok", "as_of": today, "checked_at": checked,
                                                  "count": len(rows), "sources": sources})
                return rows, {"status": "ok", "path": str(self.root / "index.json"),
                              "as_of": today, "count": len(rows), "sources": sources,
                              "source_url": sources[0]["url"], "checked_at": checked}
            except (DigestError, OSError, KeyError, TypeError) as error:
                message = str(error) if isinstance(error, DigestError) else "The external digest could not be fully verified."
                write(self.root / "status.json", {"status": "incomplete", "checked_at": checked, "error": message})
                raise DigestError(message) from None
