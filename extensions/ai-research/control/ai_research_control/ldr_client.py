from __future__ import annotations

import re
from html.parser import HTMLParser
from collections.abc import Callable
from typing import Any

import requests


MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_HTML_BYTES = 512 * 1024
MAX_EXPORT_BYTES = 64 * 1024 * 1024
RUN_ID_RE = re.compile(r"^lr_[0-9a-f]{32}$")
COLLECTION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
RESEARCH_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

# Exact static engine registry shipped by locked LDR v1.10.6. Missing
# agent_enabled settings default to True upstream, so every registry entry must
# be written explicitly to keep the V0.1 tool surface closed by default.
AGENT_TOOL_KEYS = (
    "arxiv",
    "brave",
    "ddg",
    "elasticsearch",
    "exa",
    "github",
    "google_pse",
    "guardian",
    "gutenberg",
    "mojeek",
    "nasa_ads",
    "openalex",
    "openlibrary",
    "paperless",
    "pubchem",
    "pubmed",
    "scaleserp",
    "searxng",
    "semantic_scholar",
    "serpapi",
    "serper",
    "sofya",
    "stackexchange",
    "tavily",
    "tinyfish",
    "wayback",
    "wikinews",
    "wikipedia",
    "zenodo",
)
ENABLED_ACADEMIC_TOOLS = {"arxiv", "openalex", "semantic_scholar"}


class LdrError(RuntimeError):
    pass


class LdrUnavailable(LdrError):
    pass


class LdrAuthenticationError(LdrError):
    pass


class LdrSessionExpired(LdrError):
    pass


class LdrConflict(LdrError):
    pass


class LdrProtocolError(LdrError):
    pass


class _CsrfInputParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.value: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "input":
            return
        values = {name.casefold(): value for name, value in attrs}
        if values.get("name") == "csrf_token" and values.get("value"):
            self.value = values["value"]


class LdrClient:
    """Bounded adapter for the locked Local Deep Research v1.10.6 HTTP API."""

    def __init__(
        self,
        base_url: str,
        *,
        session: requests.Session | None = None,
        session_factory: Callable[[], requests.Session] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._session_factory = session_factory or self._new_session
        self._session = session or self._session_factory()
        self._csrf_token: str | None = None
        self._username: str | None = None
        self._was_unlocked = False

    @staticmethod
    def _new_session() -> requests.Session:
        value = requests.Session()
        value.trust_env = False
        return value

    @property
    def username(self) -> str | None:
        return self._username

    def clear(self) -> None:
        self._session.close()
        self._session = self._session_factory()
        self._csrf_token = None
        self._username = None
        self._was_unlocked = False

    def probe(self) -> bool:
        try:
            response = self._session.get(
                self.base_url + "/auth/check", timeout=(2.0, 3.0)
            )
        except requests.RequestException as exc:
            raise LdrUnavailable("LDR is unavailable") from exc
        return response.status_code in {200, 401}

    def session_status(self) -> dict[str, str | None]:
        try:
            response = self._session.get(
                self.base_url + "/auth/check", timeout=(2.0, 5.0)
            )
        except requests.RequestException as exc:
            raise LdrUnavailable("LDR is unavailable") from exc
        if response.status_code == 200:
            value = self._json(response)
            if value.get("authenticated") is True and isinstance(
                value.get("username"), str
            ):
                self._username = value["username"]
                self._was_unlocked = True
                return {"status": "ready", "username": self._username}
        if response.status_code == 401:
            self._csrf_token = None
            self._username = None
            return {
                "status": "expired" if self._was_unlocked else "locked",
                "username": None,
            }
        self._raise_response(response, "checking LDR session")
        raise LdrProtocolError("invalid LDR session response")

    def unlock(self, username: str, password: str) -> dict[str, str | None]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{3,64}", username):
            raise LdrAuthenticationError("invalid LDR username")
        if not password or len(password) > 1024:
            raise LdrAuthenticationError("invalid LDR password")

        self._session.close()
        self._session = self._session_factory()
        self._csrf_token = None
        self._username = None
        try:
            login_page = self._session.get(
                self.base_url + "/auth/login", timeout=(3.0, 10.0)
            )
            self._bounded(login_page, MAX_HTML_BYTES)
            if login_page.status_code != 200:
                self._raise_response(login_page, "loading LDR login")
            parser = _CsrfInputParser()
            parser.feed(login_page.text)
            if not parser.value:
                raise LdrProtocolError("LDR login page omitted CSRF token")

            login = self._session.post(
                self.base_url + "/auth/login",
                data={
                    "username": username,
                    "password": password,
                    "csrf_token": parser.value,
                },
                allow_redirects=False,
                timeout=(3.0, 20.0),
            )
        except requests.RequestException as exc:
            raise LdrUnavailable("LDR login request failed") from exc
        if login.status_code not in {200, 302, 303}:
            if login.status_code in {400, 401, 429}:
                raise LdrAuthenticationError("LDR credentials were rejected")
            self._raise_response(login, "unlocking LDR")

        check = self._session.get(
            self.base_url + "/auth/check", timeout=(3.0, 10.0)
        )
        if check.status_code != 200:
            raise LdrAuthenticationError("LDR credentials were rejected")
        check_value = self._json(check)
        if check_value.get("authenticated") is not True:
            raise LdrAuthenticationError("LDR credentials were rejected")

        csrf = self._session.get(
            self.base_url + "/auth/csrf-token", timeout=(3.0, 10.0)
        )
        csrf_value = self._json(csrf)
        token = csrf_value.get("csrf_token")
        if not isinstance(token, str) or not token:
            raise LdrProtocolError("LDR API CSRF token is missing")
        self._csrf_token = token
        self._username = username
        self._was_unlocked = True
        return {"status": "ready", "username": username}

    def configure_fixed_profile(
        self, *, model_id: str, bridge_url: str, bridge_token: str
    ) -> None:
        if not model_id or not bridge_url or not bridge_token:
            raise LdrProtocolError("fixed literature model bridge is not configured")
        settings: dict[str, Any] = {
            "llm.provider": "openai_endpoint",
            "llm.model": model_id,
            "llm.openai_endpoint.url": bridge_url.rstrip("/"),
            "llm.openai_endpoint.api_key": bridge_token,
            "search.tool": "openalex",
            "search.engine.web.openalex.default_params.enable_llm_relevance_filter": False,
            "search.max_results": 15,
            "search.iterations": 2,
            "search.questions_per_iteration": 3,
            "search.search_strategy": "langgraph-agent",
            # search.iterations applies to pipeline strategies only. These are
            # the real LangGraph bounds in LDR v1.10.6; values below the
            # upstream minima silently expand to its defaults.
            "langgraph_agent.max_iterations": 10,
            "langgraph_agent.max_sub_iterations": 3,
            "langgraph_agent.include_sub_research": False,
            "langgraph_agent.max_subagent_workers": 1,
            "policy.egress_scope": "public_only",
            "embeddings.require_local": True,
            "local_search_embedding_provider": "sentence_transformers",
            "local_search_embedding_model": (
                "/data/models/sentence-transformers/all-MiniLM-L6-v2"
            ),
        }
        for engine in AGENT_TOOL_KEYS:
            settings[f"search.engine.web.{engine}.agent_enabled"] = (
                engine in ENABLED_ACADEMIC_TOOLS
            )
        response = self._request(
            "POST", "/settings/save_all_settings", json=settings, timeout=(3.0, 30.0)
        )
        value = self._json(response)
        if value.get("status") != "success":
            raise LdrProtocolError("LDR rejected the fixed literature profile")

    def start_research(
        self,
        *,
        question: str,
        control_run_id: str,
        model_id: str,
        bridge_url: str,
        collection_id: str | None,
    ) -> tuple[str, str]:
        if not question.strip() or len(question) > 5000:
            raise LdrProtocolError("invalid literature research question")
        if not RUN_ID_RE.fullmatch(control_run_id):
            raise LdrProtocolError("invalid literature control run id")
        if collection_id and not COLLECTION_ID_RE.fullmatch(collection_id):
            raise LdrProtocolError("invalid LDR collection id")
        response = self._request(
            "POST",
            "/api/start_research",
            json={
                "query": question.strip(),
                "mode": "deep",
                "model_provider": "openai_endpoint",
                "model": model_id,
                "custom_endpoint": bridge_url.rstrip("/"),
                # OpenAlex remains the primary engine. An eligible collection is
                # exposed separately by LDR as a LangGraph specialized tool; using
                # collection_<id> here would replace, rather than supplement, the
                # locked academic search path.
                "search_engine": "openalex",
                "max_results": 15,
                "iterations": 2,
                "questions_per_iteration": 3,
                "strategy": "langgraph-agent",
                "policy_egress_scope": "public_only",
                "metadata": {
                    "modelmirror_literature_run_id": control_run_id,
                    **(
                        {"modelmirror_collection_id": collection_id}
                        if collection_id
                        else {}
                    ),
                },
            },
            timeout=(3.0, 30.0),
        )
        value = self._json(response)
        research_id = value.get("research_id")
        if value.get("status") not in {"success", "queued"} or not isinstance(
            research_id, str
        ) or not RESEARCH_ID_RE.fullmatch(research_id):
            raise LdrProtocolError("LDR start response omitted research id")
        return research_id, str(value["status"])

    def find_research_by_run_id(self, control_run_id: str) -> str | None:
        if not RUN_ID_RE.fullmatch(control_run_id):
            raise LdrProtocolError("invalid literature control run id")
        value = self._json(self._request("GET", "/api/history?limit=500"))
        for item in value.get("items", []):
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata")
            if (
                isinstance(metadata, dict)
                and metadata.get("modelmirror_literature_run_id") == control_run_id
                and isinstance(item.get("id"), str)
            ):
                research_id = item["id"]
                if not RESEARCH_ID_RE.fullmatch(research_id):
                    raise LdrProtocolError("LDR history returned an unsafe research id")
                return research_id
        return None

    def research_status(self, research_id: str) -> dict[str, Any]:
        self._validate_research_id(research_id)
        return self._json(self._request("GET", f"/api/research/{research_id}/status"))

    def report(self, research_id: str) -> dict[str, Any]:
        self._validate_research_id(research_id)
        return self._json(self._request("GET", f"/api/report/{research_id}"))

    def terminate(self, research_id: str) -> dict[str, Any]:
        self._validate_research_id(research_id)
        return self._json(
            self._request("POST", f"/api/terminate/{research_id}", json={})
        )

    def export(self, research_id: str, export_format: str) -> bytes:
        self._validate_research_id(research_id)
        if export_format not in {"quarto", "ris"}:
            raise LdrProtocolError("unsupported LDR export format")
        response = self._request(
            "POST",
            f"/api/v1/research/{research_id}/export/{export_format}",
            json={},
            timeout=(3.0, 60.0),
        )
        self._bounded(response, MAX_EXPORT_BYTES)
        return response.content

    def collections(self) -> list[dict[str, Any]]:
        value = self._json(self._request("GET", "/library/api/collections"))
        items = value.get("collections")
        if value.get("success") is not True or not isinstance(items, list):
            raise LdrProtocolError("invalid LDR collections response")
        return [item for item in items if isinstance(item, dict)]

    def zotero_config(self) -> dict[str, Any]:
        return self._json(self._request("GET", "/library/api/zotero/config"))

    def zotero_status(self) -> dict[str, Any]:
        return self._json(self._request("GET", "/library/api/zotero/status"))

    def zotero_sync(self) -> dict[str, Any]:
        return self._json(
            self._request("POST", "/library/api/zotero/sync", json={})
        )

    def index_collection(self, collection_id: str) -> list[dict[str, Any]]:
        if not COLLECTION_ID_RE.fullmatch(collection_id):
            raise LdrProtocolError("invalid LDR collection id")
        response = self._request(
            "GET",
            f"/library/api/collections/{collection_id}/index",
            stream=True,
            timeout=(3.0, 300.0),
        )
        events: list[dict[str, Any]] = []
        total = 0
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            encoded = line[6:].encode("utf-8")
            total += len(encoded)
            if total > MAX_JSON_BYTES or len(events) >= 10_000:
                raise LdrProtocolError("LDR index event stream exceeded limits")
            try:
                import json

                value = json.loads(encoded)
            except (ValueError, UnicodeDecodeError) as exc:
                raise LdrProtocolError("invalid LDR index event") from exc
            if isinstance(value, dict):
                events.append(value)
        if not events or events[-1].get("type") not in {"complete", "error"}:
            raise LdrProtocolError("LDR index stream ended without a terminal event")
        return events

    @staticmethod
    def _validate_research_id(research_id: str) -> None:
        if not isinstance(research_id, str) or not RESEARCH_ID_RE.fullmatch(
            research_id
        ):
            raise LdrProtocolError("invalid LDR research id")

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        headers = dict(kwargs.pop("headers", {}))
        if method != "GET":
            if not self._csrf_token:
                raise LdrSessionExpired("LDR session is not unlocked")
            self._refresh_csrf_token()
            headers["X-CSRF-Token"] = self._csrf_token
        try:
            response = self._session.request(
                method,
                self.base_url + path,
                headers=headers,
                timeout=kwargs.pop("timeout", (3.0, 20.0)),
                allow_redirects=False,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise LdrUnavailable("LDR request failed") from exc
        if response.status_code < 200 or response.status_code >= 300:
            self._raise_response(response, "calling LDR")
        return response

    def _refresh_csrf_token(self) -> None:
        try:
            response = self._session.get(
                self.base_url + "/auth/csrf-token", timeout=(3.0, 10.0)
            )
        except requests.RequestException as exc:
            raise LdrUnavailable("LDR CSRF refresh failed") from exc
        if response.status_code != 200:
            self._raise_response(response, "refreshing LDR CSRF token")
        value = self._json(response)
        token = value.get("csrf_token")
        if not isinstance(token, str) or not token:
            raise LdrProtocolError("LDR API CSRF token is missing")
        self._csrf_token = token

    def _json(self, response: requests.Response) -> dict[str, Any]:
        self._bounded(response, MAX_JSON_BYTES)
        try:
            value = response.json()
        except ValueError as exc:
            raise LdrProtocolError("LDR returned malformed JSON") from exc
        if not isinstance(value, dict):
            raise LdrProtocolError("LDR returned a non-object JSON response")
        return value

    @staticmethod
    def _bounded(response: requests.Response, limit: int) -> None:
        header = response.headers.get("content-length")
        if header:
            try:
                if int(header) > limit:
                    raise LdrProtocolError("LDR response exceeded size limit")
            except ValueError as exc:
                raise LdrProtocolError("LDR returned invalid content length") from exc
        if len(response.content) > limit:
            raise LdrProtocolError("LDR response exceeded size limit")

    def _raise_response(self, response: requests.Response, operation: str) -> None:
        if response.status_code == 401:
            self._csrf_token = None
            self._username = None
            self._was_unlocked = True
            raise LdrSessionExpired("LDR session expired")
        if response.status_code in {409, 429}:
            raise LdrConflict(f"LDR refused {operation}")
        if response.status_code >= 500:
            raise LdrUnavailable(f"LDR failed while {operation}")
        raise LdrProtocolError(f"LDR rejected {operation}")
