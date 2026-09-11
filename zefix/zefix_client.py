"""Minimal client for the Zefix Public REST API (Swiss commercial register).

Standard library only, so the module runs in a workshop notebook or a bare
container without an install step.

The API is documented at
https://www.zefix.admin.ch/ZefixPublicREST/swagger-ui/index.html and requires
HTTP Basic credentials issued by the EHRA (see README.md). Base URLs:

    PROD  https://www.zefix.admin.ch/ZefixPublicREST/api/v1
    TEST  https://www.zefixintg.admin.ch/ZefixPublicREST/api/v1

Endpoint paths marked "unverified" in ENDPOINTS below were taken from
third-party clients rather than the official specification. Run
``python -m zefix.explore --probe`` with credentials to confirm them against
the live service; the probe reports the status code of each candidate path.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

PROD_BASE_URL = "https://www.zefix.admin.ch/ZefixPublicREST/api/v1"
TEST_BASE_URL = "https://www.zefixintg.admin.ch/ZefixPublicREST/api/v1"

#: Path of the OpenAPI document, relative to the service root (not /api/v1).
OPENAPI_PATH = "/ZefixPublicREST/v3/api-docs"

#: Candidate endpoints, used by the explore script to probe the live service.
#: (method, path template, description, verified-against-official-docs)
ENDPOINTS: tuple[tuple[str, str, str, bool], ...] = (
    ("POST", "/company/search", "Search companies by name/canton/legal form", True),
    ("GET", "/company/uid/{uid}", "Company detail by UID (CHE-xxx.xxx.xxx)", True),
    ("GET", "/company/chid/{chid}", "Company detail by cantonal CH-ID", True),
    ("GET", "/company/ehraid/{ehraid}", "Company detail by EHRA surrogate id", True),
    ("GET", "/sogc/bydate/{date}", "All SOGC/SHAB publications for one day", True),
    ("GET", "/legalForm", "Reference list of legal forms", False),
    ("GET", "/community", "Reference list of political communities", False),
    ("GET", "/registryOffice", "Reference list of cantonal registry offices", False),
    (
        "GET",
        "/registryOffice/bfsCommunityId/{bfsId}",
        "Registry office serving a BFS community id",
        False,
    ),
)


class ZefixError(RuntimeError):
    """An HTTP or transport error raised by the Zefix API."""

    def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass
class ZefixClient:
    """Thin, throttled wrapper around the Zefix Public REST API.

    Every endpoint requires Basic authentication, so ``username`` and
    ``password`` are mandatory for anything other than :meth:`openapi_spec`.
    """

    username: str
    password: str
    base_url: str = PROD_BASE_URL
    #: Minimum seconds between two requests. The service is rate limited and
    #: EHRA asks integrators to stay gentle; 0.5 s is a conservative default.
    min_interval: float = 0.5
    timeout: float = 30.0
    max_retries: int = 3
    user_agent: str = "zefix-explorer/1.0 (+research)"
    _last_request_at: float = field(default=0.0, init=False, repr=False)

    # ---------------------------------------------------------------- plumbing

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at and elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_at = time.monotonic()

    def _auth_header(self) -> str:
        raw = f"{self.username}:{self.password}".encode()
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        base_url: str | None = None,
    ) -> Any:
        """Perform one API call and return the decoded JSON payload.

        Retries on 429 and 5xx with exponential backoff; raises
        :class:`ZefixError` on any other failure.
        """
        url = (base_url or self.base_url).rstrip("/") + path
        query = {k: v for k, v in (params or {}).items() if v is not None}
        if query:
            url += "?" + urllib.parse.urlencode(query)

        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Accept": "application/json",
            "Authorization": self._auth_header(),
            "User-Agent": self.user_agent,
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"

        last_error: ZefixError | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            req = urllib.request.Request(url, data=payload, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    text = resp.read().decode("utf-8")
                return json.loads(text) if text.strip() else None
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:500]
                last_error = ZefixError(
                    f"{method} {url} -> HTTP {exc.code}", exc.code, detail
                )
                # 401/403/404 will not fix themselves; fail fast.
                if exc.code not in (429, 500, 502, 503, 504):
                    raise last_error from exc
            except urllib.error.URLError as exc:
                last_error = ZefixError(f"{method} {url} -> {exc.reason}")
            if attempt < self.max_retries:
                time.sleep(2**attempt)

        assert last_error is not None
        raise last_error

    # --------------------------------------------------------------- endpoints

    def search_companies(
        self,
        name: str,
        *,
        canton: str | None = None,
        legal_form_id: int | None = None,
        active_only: bool = True,
        max_entries: int = 30,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Search the index by company name.

        ``name`` supports the same wildcard handling as the Zefix web search.
        Returns a list of match stubs (name, uid, chid, ehraid, legal form,
        registry office, status) - not full company records.
        """
        body: dict[str, Any] = {
            "name": name,
            "activeOnly": active_only,
            "maxEntries": max_entries,
            "offset": offset,
        }
        if canton:
            body["canton"] = canton
        if legal_form_id is not None:
            body["legalFormId"] = legal_form_id
        return self.request("POST", "/company/search", body=body) or []

    def company_by_uid(self, uid: str, *, language: str | None = None) -> Any:
        """Full company record by UID, e.g. ``CHE-105.805.185``."""
        return self.request(
            "GET",
            f"/company/uid/{urllib.parse.quote(uid)}",
            params={"languageKey": language},
        )

    def company_by_chid(self, chid: str, *, language: str | None = None) -> Any:
        """Full company record by cantonal CH-ID."""
        return self.request(
            "GET",
            f"/company/chid/{urllib.parse.quote(chid)}",
            params={"languageKey": language},
        )

    def company_by_ehraid(self, ehraid: int | str, *, language: str | None = None) -> Any:
        """Full company record by EHRA surrogate identifier."""
        return self.request(
            "GET",
            f"/company/ehraid/{urllib.parse.quote(str(ehraid))}",
            params={"languageKey": language},
        )

    def sogc_by_date(self, date: str) -> Any:
        """All SOGC/SHAB publications for one ISO date (``YYYY-MM-DD``).

        This is the endpoint to poll for a daily delta feed of register
        mutations across all 26 cantons.
        """
        return self.request("GET", f"/sogc/bydate/{urllib.parse.quote(date)}")

    def legal_forms(self) -> Any:
        """Reference list of legal forms (AG, GmbH, ...) with their ids."""
        return self.request("GET", "/legalForm")

    def communities(self) -> Any:
        """Reference list of political communities with BFS ids."""
        return self.request("GET", "/community")

    def registry_offices(self) -> Any:
        """Reference list of cantonal commercial registry offices."""
        return self.request("GET", "/registryOffice")

    # ------------------------------------------------------------ spec helper

    def openapi_spec(self, service_root: str | None = None) -> Any:
        """Fetch the OpenAPI document that authoritatively describes the API."""
        root = service_root or self.base_url.split("/ZefixPublicREST")[0]
        return self.request("GET", OPENAPI_PATH, base_url=root)
