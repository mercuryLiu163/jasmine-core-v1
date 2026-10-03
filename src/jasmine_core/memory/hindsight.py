"""The real Hindsight transport: HTTP over the standard library (ADR 0011 §2).

Everything asserted below was read from the official OpenAPI document of
Hindsight **0.10.2** (`https://hindsight.vectorize.io/openapi.json`,
`info.version = 0.10.2`, retrieved 2026-09-29) and is cited so a reviewer can
re-check it against a live deployment rather than take it on trust:

| Operation | Request | Response |
| --- | --- | --- |
| version | `GET /version` | `{api_version, features{observations, mcp, worker, store_document_text, ...}}` |
| retain | `POST /v1/default/banks/{bank_id}/memories` | `{success, bank_id, items_count, async, operation_id, usage}` |
| recall | `POST /v1/default/banks/{bank_id}/memories/recall` | `{results[], chunks, source_facts}` |
| reflect | `POST /v1/default/banks/{bank_id}/reflect` | `{text, based_on, usage}` |
| list units | `GET /v1/default/banks/{bank_id}/memories/list?document_id=` | `{items[], total, limit, offset}` |
| invalidate unit | `PATCH /v1/default/banks/{bank_id}/memories/{memory_id}` `{"state":"invalidated"}` | memory unit |
| list documents | `GET /v1/default/banks/{bank_id}/documents` | `{items[], total, ...}` |
| delete document | `DELETE /v1/default/banks/{bank_id}/documents/{document_id}` | `{success, message, document_id, memory_units_deleted}` |

Five consequences of that spec drive the whole design, and each is a decision
rather than an implementation detail:

1. **`document_id` is the idempotency key, and `update_mode="replace"` is its
   upsert.** An item re-sent with the same `document_id` has the previous
   document *and all its memories* deleted, then re-extracted. So a re-send is
   safe to repeat but it renumbers the memory units, and a remote memory unit id
   is therefore not a stable handle. `types.RetainReceipt` records the document
   id, never a memory unit id, and every lifecycle/read-back path addresses
   units by listing the document.

2. **Retain is synchronous on purpose.** `usage` and `items_count` are only
   present for `async=false`; `operation_id` only for `async=true`. A durable
   worker needs a *completion* receipt, not an acknowledgement that work was
   queued, so this transport always sends `async=false`. The async path's
   `operation_id` retry safety is therefore not relied upon.

3. **Recall's `types` is the engine's vocabulary, not ours.** The engine knows
   `world`, `experience` and `observation`. Our five memory types have no remote
   equivalent, so they travel as `metadata` (returned on every recalled result
   per `RecallResult.metadata`) and as `tags` (used for the visibility filter).
   `observation` is deliberately never requested: an observation is an
   LLM-consolidated belief with no path back to the Raw Event that produced it,
   and design §16 forbids a high-authority fact that cannot be traced. This is
   `recall_fact_types()`.

4. **Soft-retire is a first-class engine operation.** `PATCH
   /memories/{id}` with `state="invalidated"` is documented as "excluded from
   recall/consolidation, links and derived observations pruned, moved to the
   archive… Reversible". That is exactly what design §9's `obsolete` means, so
   `obsolete` is local-first plus a best-effort remote invalidate, and only
   `DELETE /documents/{id}` is a physical removal.

5. **Authentication is not part of the base API.** The 0.10.2 spec declares no
   `securitySchemes`; every path takes an optional `Authorization` header that a
   tenant extension validates. So a single-user deployment needs no credential
   and a multi-tenant one needs an operator-provided key, and this transport
   sends the header only when one is configured. Credentials come from the
   environment, never from argv, a request body or a database row, and are never
   logged or returned (design §12).

`/v1/default` is the API prefix in 0.10.2 and is configurable, because it is a
deployment route rather than a contract. Redirects are *not* followed: a 30x
would replay the `Authorization` header at a host the operator did not name.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .. import errors
from . import filtering
from .transport import TransportError
from .types import BankRef, EngineResult

ENGINE_ID = "hindsight"

URL_ENV_VAR = "JASMINE_HINDSIGHT_URL"
API_KEY_ENV_VAR = "JASMINE_HINDSIGHT_API_KEY"
TIMEOUT_ENV_VAR = "JASMINE_HINDSIGHT_TIMEOUT_SECONDS"
API_PREFIX_ENV_VAR = "JASMINE_HINDSIGHT_API_PREFIX"

DEFAULT_API_PREFIX = "/v1/default"
DEFAULT_TIMEOUT_SECONDS = 20.0

#: A recall answer larger than this is a remote anomaly, not a memory. Reading
#: it would let one response exhaust the Core process, so the read is capped and
#: the condition is reported as a protocol error rather than truncated silently.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024

#: Hindsight rejects a recall query over 500 tokens. Core checks first so the
#: refusal is a Core 400 with a clear field name instead of a remote 422.
MAX_QUERY_CHARS = 2000

#: Hindsight's own default recall budget is 4096 tokens. A reflect request is an
#: LLM call over the whole bank, so an unbounded `max_tokens` from an operator
#: body would be an unbounded bill. The Memory Context-Pack budget is 400-600
#: (design §8), so this is generous for any legitimate use.
MAX_REQUEST_TOKENS = 8192

#: The engine's own fact vocabulary, in the order Core prefers to receive them.
#: `observation` is absent on purpose; see the module docstring, point 3.
FACT_TYPES: tuple[str, ...] = ("world", "experience")

#: Verified against the 0.10.2 spec on 2026-09-29. If a deployment reports a
#: different `api_version`, `check_supported` says so rather than guessing.
VERIFIED_API_VERSIONS = ("0.10",)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to replay credentials at a host the operator did not configure.

    A 3xx from the engine is unexpected: the operator named one origin, and
    following a redirect would re-send the `Authorization` header — and every
    byte of retained content — somewhere they did not.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise TransportError(
            "engine_unexpected_redirect",
            f"the engine answered {code} and redirected to another location",
            retryable=False, http_status=code,
        )


class _StrictErrorProcessor(urllib.request.HTTPErrorProcessor):
    """Turn 3xx into a refusal instead of following it.

    This override is the load-bearing part. `HTTPErrorProcessor` is itself a
    subclass of `HTTPRedirectHandler` and handles 301/302/303/307 in
    `http_response` — which runs *before* any `http_error_30x` handler, so
    replacing `HTTPRedirectHandler` alone looks like it disables redirects and
    does not: the credential would still be replayed at the new location.
    Passing this class to `build_opener` is what actually removes the default
    processor, and it is why `_NoRedirect` below is belt and braces.
    """

    def http_response(self, request, response):  # noqa: D102
        code = int(getattr(response, "status", None) or getattr(response, "code", 0) or 0)
        if 300 <= code < 400:
            raise TransportError(
                "engine_unexpected_redirect",
                f"the engine answered {code} and redirected to another location",
                retryable=False, http_status=code,
            )
        return super().http_response(request, response)


def _strict_opener() -> urllib.request.OpenerDirector:
    """A URL opener that refuses redirects and still reports 4xx/5xx as errors.

    `build_opener` drops a default handler class when one of the supplied
    handlers is a subclass of it, which is how both `HTTPRedirectHandler` and
    `HTTPErrorProcessor` are replaced here. Dropping the error processor instead
    would be wrong: without it a 503 is returned as an ordinary response body and
    the failure would surface as a confusing parse error instead of a stable
    `engine_server_error`.
    """
    return urllib.request.build_opener(_NoRedirect(), _StrictErrorProcessor())


class HindsightConfig:
    """Operator-provided engine settings. Holds no credential that can be printed.

    `origin_host` is reduced to `scheme://host:port` on the way in: a URL with
    `user:pass@` or a query string would otherwise put a credential into
    `memory_engine_status` and out through the health API.
    """

    __slots__ = ("base_url", "api_prefix", "timeout_seconds", "_api_key", "origin_host")

    def __init__(self, base_url: str, *, api_prefix: str = DEFAULT_API_PREFIX,
                 timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
                 api_key: str | None = None) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("JASMINE_HINDSIGHT_URL must be an absolute http(s) URL")
        # Rebuild without userinfo, query or fragment: only the origin is ever
        # stored or reported.
        netloc = parsed.hostname if not parsed.port else f"{parsed.hostname}:{parsed.port}"
        self.base_url = urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
        self.origin_host = f"{parsed.scheme}://{netloc}"
        self.api_prefix = "/" + api_prefix.strip("/") if api_prefix.strip("/") else ""
        self.timeout_seconds = float(timeout_seconds)
        self._api_key = api_key or None

    @property
    def has_credential(self) -> bool:
        return self._api_key is not None

    def headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "jasmine-core-p4"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return f"HindsightConfig(origin_host={self.origin_host!r}, api_prefix={self.api_prefix!r})"


def config_from_env(env: dict[str, str] | None = None) -> HindsightConfig | None:
    """Read the operator's configuration, or None when the engine is not set up.

    Returning None is a normal state, not an error: a Core with no Hindsight
    configured still serves Rules, State, Evidence and Checkpoints, and reports
    Memory as unconfigured rather than as unhealthy.
    """
    source = os.environ if env is None else env
    base_url = (source.get(URL_ENV_VAR) or "").strip()
    if not base_url:
        return None
    try:
        timeout = float(source.get(TIMEOUT_ENV_VAR) or DEFAULT_TIMEOUT_SECONDS)
    except ValueError:
        timeout = DEFAULT_TIMEOUT_SECONDS
    try:
        return HindsightConfig(
            base_url,
            api_prefix=(source.get(API_PREFIX_ENV_VAR) or DEFAULT_API_PREFIX),
            timeout_seconds=timeout,
            api_key=(source.get(API_KEY_ENV_VAR) or None),
        )
    except ValueError as exc:
        # A typo in the operator's URL is a configuration error with a code and a
        # message the operator can act on, not an unhandled `ValueError` that
        # surfaces as a 500 on whichever endpoint happened to touch it.
        raise errors.InvalidRequest(
            f"{URL_ENV_VAR} is not a usable engine URL: {exc}",
            field=URL_ENV_VAR,
        ) from exc


def recall_fact_types(memory_types: tuple[str, ...]) -> tuple[str, ...]:
    """Map requested Core memory types onto the engine's fact vocabulary.

    Core filters by `memory_type` locally from `metadata`, so every request asks
    for both raw fact families and the narrowing happens where the provenance can
    be checked. There is no Core memory type that maps to `observation`, and
    none ever will: an observation cannot be traced to a Raw Event.
    """
    return FACT_TYPES


class HindsightTransport:
    """`MemoryTransport` over Hindsight's real HTTP API."""

    engine_id = ENGINE_ID

    def __init__(self, config: HindsightConfig) -> None:
        self._config = config
        # A dedicated opener so the no-redirect rule and the timeout apply to
        # every engine call and to nothing else in the process.
        self._opener = _strict_opener()

    @property
    def config(self) -> HindsightConfig:
        return self._config

    # -- low level ---------------------------------------------------------

    def _url(self, path: str, query: dict[str, Any] | None = None, *,
             rooted: bool = False) -> str:
        """Absolute URL for a path.

        `rooted=True` is for the endpoints that live outside the versioned API
        prefix. In Hindsight 0.10.2 `/version` is a root path, not
        `/v1/default/version`; probing the wrong one answers 404 and would be
        reported as an unreachable engine.
        """
        prefix = "" if rooted else self._config.api_prefix
        url = f"{self._config.base_url}{prefix}{path}"
        if query:
            filtered = {k: v for k, v in query.items() if v is not None}
            if filtered:
                url = f"{url}?{urllib.parse.urlencode(filtered, doseq=True)}"
        return url

    def _request(self, method: str, path: str, *, query: dict[str, Any] | None = None,
                 body: dict[str, Any] | None = None, rooted: bool = False) -> Any:
        data = None
        headers = self._config.headers()
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(self._url(path, query, rooted=rooted), data=data,
                                         headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self._config.timeout_seconds) as response:
                payload = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, socket.timeout):
                raise TransportError(
                    "engine_timeout",
                    f"the engine did not answer within {self._config.timeout_seconds:g}s",
                    retryable=True,
                ) from exc
            raise TransportError(
                "engine_unreachable",
                filtering.safe_summary(f"could not reach the engine: {reason}"),
                retryable=True,
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise TransportError(
                "engine_timeout",
                f"the engine did not answer within {self._config.timeout_seconds:g}s",
                retryable=True,
            ) from exc
        except OSError as exc:
            raise TransportError(
                "engine_unreachable",
                filtering.safe_summary(f"could not reach the engine: {exc}"),
                retryable=True,
            ) from exc
        if len(payload) > MAX_RESPONSE_BYTES:
            raise TransportError("engine_response_too_large",
                                 f"the engine response exceeded {MAX_RESPONSE_BYTES} bytes",
                                 retryable=False)
        if not payload:
            return {}
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            # The body is not quoted: it is engine output of unknown provenance
            # and could contain anything.
            raise TransportError("engine_protocol_error",
                                 "the engine response was not valid JSON",
                                 retryable=False) from exc

    def _http_error(self, exc: urllib.error.HTTPError) -> TransportError:
        status = exc.code
        try:
            body = exc.read(4096).decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - defensive
            body = ""
        if status == 401:
            return TransportError("engine_unauthorized",
                                  "the engine rejected the credential", retryable=False,
                                  http_status=status)
        if status == 403:
            return TransportError("engine_forbidden",
                                  "the engine refused this bank or operation", retryable=False,
                                  http_status=status)
        if status == 404:
            return TransportError("engine_not_found",
                                  filtering.safe_summary(body) or "the engine has no such object",
                                  retryable=False, http_status=status)
        if status == 409:
            return TransportError("engine_conflict",
                                  filtering.safe_summary(body) or "the engine reported a conflict",
                                  retryable=False, http_status=status)
        if status == 422:
            return TransportError("engine_rejected",
                                  filtering.safe_summary(body) or "the engine rejected the request",
                                  retryable=False, http_status=status)
        if status == 429:
            return TransportError("engine_rate_limited",
                                  filtering.safe_summary(body) or "the engine is rate limiting",
                                  retryable=True, http_status=status)
        if 500 <= status < 600:
            return TransportError("engine_server_error",
                                  filtering.safe_summary(body) or "the engine failed",
                                  retryable=True, http_status=status)
        return TransportError("engine_http_error",
                              f"unexpected HTTP {status} from the engine",
                              retryable=False, http_status=status)

    def _bank_path(self, bank: BankRef, suffix: str) -> str:
        # `bank_id` is validated as a closed character class by
        # `memory.scope` before it ever reaches here, and is additionally
        # percent-encoded so a future prefix cannot become a path traversal.
        return f"/banks/{urllib.parse.quote(bank.bank_id, safe='')}{suffix}"

    # -- MemoryTransport ---------------------------------------------------

    def version(self) -> dict[str, Any]:
        """`GET /version`. Never raises: health must be reportable, not fatal."""
        try:
            payload = self._request("GET", "/version", rooted=True)
        except TransportError as exc:
            return {"reachable": False, "engine_id": ENGINE_ID,
                    "origin_host": self._config.origin_host,
                    "error_code": exc.code, "safe_error_summary": exc.safe_error_summary}
        if not isinstance(payload, dict):
            # A reachable engine that answered with something other than an
            # object is not a usable engine, and health must be reportable.
            return {"reachable": False, "engine_id": ENGINE_ID,
                    "origin_host": self._config.origin_host,
                    "error_code": "engine_protocol_error",
                    "safe_error_summary": "the version response was not a JSON object"}
        features = payload.get("features")
        return {
            "reachable": True,
            "engine_id": ENGINE_ID,
            "origin_host": self._config.origin_host,
            "api_version": payload.get("api_version")
            if isinstance(payload.get("api_version"), str) else None,
            "features": features if isinstance(features, dict) else {},
        }

    def retain(self, *, bank: BankRef, document_id: str, content: str, context: str,
               metadata: dict[str, str], tags: tuple[str, ...], memory_type: str,
               timestamp: str | None) -> dict[str, Any]:
        """`POST /v1/default/banks/{bank_id}/memories` with `async=false`.

        `update_mode="replace"` is the documented default and is stated
        explicitly so a future engine default change cannot silently turn a
        retry into a second copy of the same document.
        """
        item: dict[str, Any] = {
            "content": content,
            "context": context,
            "document_id": document_id,
            "update_mode": "replace",
        }
        if metadata:
            item["metadata"] = dict(metadata)
        if tags:
            item["tags"] = list(tags)
        if timestamp:
            item["timestamp"] = timestamp
        payload = self._request("POST", self._bank_path(bank, "/memories"),
                                body={"items": [item], "async": False})
        if not isinstance(payload, dict):
            raise TransportError("engine_protocol_error",
                                 "the retain response was not a JSON object", retryable=False)
        # RetainResponse makes `success`, `bank_id`, `items_count` and `async`
        # **required** (official OpenAPI 0.10.2). A response missing any of them
        # is not a near-miss — it is one this adapter cannot interpret — and
        # accepting it is what let an empty `{}` and a `"success": null` be
        # recorded as a completed retain: job `DONE`, item `RETAINED`, an
        # Inspector reporting a document that was never stored.
        _require_fields(payload, ("success", "bank_id", "items_count", "async"), "retain")
        if not isinstance(payload["success"], bool) or not isinstance(payload["async"], bool):
            raise TransportError(
                "engine_protocol_error",
                "`success` and `async` must be JSON booleans",
                retryable=False)
        if isinstance(payload["items_count"], bool) or not isinstance(payload["items_count"], int):
            raise TransportError(
                "engine_protocol_error",
                "`items_count` must be a JSON integer",
                retryable=False)
        if not isinstance(payload["bank_id"], str):
            raise TransportError(
                "engine_protocol_error",
                "`bank_id` must be a JSON string",
                retryable=False)
        if not payload["success"]:
            raise TransportError("engine_retain_failed",
                                 "the engine reported the retain did not succeed",
                                 retryable=True)
        if payload["async"]:
            # We asked for `async=false` precisely because a durable worker needs
            # a receipt it can trust. An operation that is only queued has no
            # confirmed result yet, and recording it as stored would be the
            # same lie as an empty receipt.
            raise TransportError(
                "engine_retain_unconfirmed",
                "the engine queued the retain instead of storing it; the document "
                "has not been confirmed and must not be marked retained",
                retryable=True)
        if payload["bank_id"] != bank.bank_id:
            # Acknowledging a different bank is not an acknowledgement of this
            # one. Recording it would let a retained document be filed under a
            # receipt that says it went somewhere else.
            raise TransportError(
                "engine_protocol_error",
                "the retain response acknowledged a different bank than the one "
                "the document was written to",
                retryable=False)
        usage = payload.get("usage")
        return {
            "document_id": document_id,
            "bank_id": payload.get("bank_id") or bank.bank_id,
            "items_count": _as_int(payload.get("items_count")),
            "engine_api_version": None,
            "operation_id": payload.get("operation_id"),
            "async": bool(payload.get("async")),
            "usage": {k: _as_int(v) for k, v in usage.items()} if isinstance(usage, dict) else {},
        }

    def recall(self, *, bank: BankRef, query: str, max_tokens: int,
               fact_types: tuple[str, ...], tags: tuple[str, ...]) -> list[EngineResult]:
        """`POST /v1/default/banks/{bank_id}/memories/recall`."""
        if len(query) > MAX_QUERY_CHARS:
            raise TransportError("query_too_long",
                                 f"the recall query exceeds {MAX_QUERY_CHARS} characters",
                                 retryable=False)
        body: dict[str, Any] = {
            "query": query,
            "max_tokens": min(max(1, int(max_tokens)), MAX_REQUEST_TOKENS),
            "include": {"entities": None, "chunks": None, "source_facts": None},
        }
        if fact_types:
            body["types"] = list(fact_types)
        if tags:
            # `any` includes untagged rows; `any_strict` would drop them. An
            # untagged row is suspicious and is filtered locally as
            # `unverified_provenance`, so it must arrive rather than be hidden
            # by a remote predicate.
            body["tags"] = list(tags)
            body["tags_match"] = "any"
        payload = self._request("POST", self._bank_path(bank, "/memories/recall"), body=body)
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise TransportError("engine_protocol_error",
                                 "the recall response had no results array", retryable=False)
        results: list[EngineResult] = []
        for row in payload["results"]:
            if not isinstance(row, dict):
                continue
            text = row.get("text")
            engine_id = row.get("id")
            if not isinstance(text, str) or not isinstance(engine_id, str):
                # A row without text or without an id cannot be traced or
                # budgeted. Dropping it here means it never reaches Core.
                continue
            metadata = row.get("metadata")
            row_tags = row.get("tags")
            scores = row.get("scores")
            final = scores.get("final") if isinstance(scores, dict) else None
            results.append(EngineResult(
                engine_result_id=engine_id,
                text=text,
                bank_id=bank.bank_id,
                document_id=row.get("document_id") if isinstance(row.get("document_id"), str) else None,
                engine_fact_type=row.get("type") if isinstance(row.get("type"), str) else None,
                metadata={k: v for k, v in metadata.items() if isinstance(k, str) and isinstance(v, str)}
                if isinstance(metadata, dict) else {},
                tags=tuple(t for t in row_tags if isinstance(t, str)) if isinstance(row_tags, list) else (),
                score=final if isinstance(final, (int, float)) else None,
            ))
        return results

    def reflect(self, *, bank: BankRef, query: str, max_tokens: int) -> dict[str, Any]:
        """`POST /v1/default/banks/{bank_id}/reflect`."""
        payload = self._request(
            "POST", self._bank_path(bank, "/reflect"),
            body={"query": query, "max_tokens": min(max(1, int(max_tokens)),
                                                  MAX_REQUEST_TOKENS)},
        )
        if not isinstance(payload, dict):
            raise TransportError("engine_protocol_error",
                                 "the reflect response was not a JSON object", retryable=False)
        based_on = payload.get("based_on")
        memory_ids: list[str] = []
        if isinstance(based_on, dict):
            for fact in based_on.get("memories") or []:
                if isinstance(fact, dict) and isinstance(fact.get("id"), str):
                    memory_ids.append(fact["id"])
        return {"text": payload.get("text") if isinstance(payload.get("text"), str) else "",
                "based_on_ids": memory_ids}

    def list_document_memories(self, *, bank: BankRef, document_id: str,
                               limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        """`GET /v1/default/banks/{bank_id}/memories/list?document_id=...`."""
        payload = self._request(
            "GET", self._bank_path(bank, "/memories/list"),
            query={"document_id": document_id, "limit": limit, "offset": offset},
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise TransportError("engine_protocol_error",
                                 "the memory listing had no items array", retryable=False)
        return [row for row in payload["items"] if isinstance(row, dict)]

    def invalidate_memory(self, *, bank: BankRef, memory_id: str, reason: str) -> None:
        """`PATCH /v1/default/banks/{bank_id}/memories/{memory_id}`."""
        self._request(
            "PATCH",
            self._bank_path(bank, f"/memories/{urllib.parse.quote(memory_id, safe='')}"),
            body={"state": "invalidated", "reason": filtering.safe_summary(reason, limit=120)},
        )

    def delete_document(self, *, bank: BankRef, document_id: str) -> dict[str, Any]:
        """`DELETE /v1/default/banks/{bank_id}/documents/{document_id}`."""
        payload = self._request(
            "DELETE",
            self._bank_path(bank, f"/documents/{urllib.parse.quote(document_id, safe='')}"),
        )
        if not isinstance(payload, dict):
            raise TransportError("engine_protocol_error",
                                 "the delete response was not a JSON object", retryable=False)
        # DeleteDocumentResponse makes `success`, `message`, `document_id` and
        # `memory_units_deleted` **required**. It used to be read as
        # `bool(payload.get("success", True))`, so an absent field and an
        # explicit `false` both came out as success, and `document_id` was
        # echoed back unexamined — an engine reporting that it deleted
        # *somebody else's* document was recorded as this one's removal.
        _require_fields(payload, ("success", "message", "document_id",
                                  "memory_units_deleted"), "delete")
        if not isinstance(payload["success"], bool):
            raise TransportError("engine_protocol_error",
                                 "`success` must be a JSON boolean", retryable=False)
        if not isinstance(payload["message"], str):
            raise TransportError("engine_protocol_error",
                                 "`message` must be a JSON string", retryable=False)
        if isinstance(payload["memory_units_deleted"], bool) or not isinstance(
                payload["memory_units_deleted"], int):
            raise TransportError("engine_protocol_error",
                                 "`memory_units_deleted` must be a JSON integer",
                                 retryable=False)
        if not payload["success"]:
            raise TransportError(
                "engine_delete_failed",
                "the engine reported the document was not deleted",
                retryable=True)
        if payload["document_id"] != document_id:
            # The engine's answer is about a different document, so it is not
            # evidence about this one. Recording `DELETED` from it would leave
            # the document we actually asked about in place while Core reports
            # it gone.
            raise TransportError(
                "engine_protocol_error",
                "the delete response named a different document than the one "
                "that was deleted",
                retryable=False)
        return {
            "document_id": payload["document_id"],
            "memory_units_deleted": payload["memory_units_deleted"],
            "success": True,
            "message": payload["message"],
        }

    def get_document(self, *, bank: BankRef, document_id: str) -> dict[str, Any] | None:
        """`GET /v1/default/banks/{bank_id}/documents/{document_id}`; None when absent."""
        try:
            payload = self._request(
                "GET",
                self._bank_path(bank, f"/documents/{urllib.parse.quote(document_id, safe='')}"),
            )
        except TransportError as exc:
            if exc.http_status == 404:
                return None
            raise
        return payload if isinstance(payload, dict) else None


def _require_fields(payload: dict[str, Any], names: tuple[str, ...], what: str) -> None:
    """The schema says these are required; a response without them is refused.

    Absent and `null` are the same refusal: neither is a value, and a worker
    that cannot tell whether a document was stored must not record that it was.
    """
    missing = [name for name in names if payload.get(name) is None]
    if missing:
        raise TransportError(
            "engine_protocol_error",
            f"the {what} response is missing required field(s): {', '.join(missing)}",
            retryable=False)


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def check_supported(version_payload: dict[str, Any]) -> tuple[bool, str | None]:
    """Whether this transport's assumptions hold for the probed deployment.

    Returns `(ok, reason)`. Two things are checked, both of which would silently
    corrupt a Gate result if they changed underneath us: the reported API
    version, and whether the deployment is storing raw source text. A
    deployment with `store_document_text=false` cannot support the G07
    delete read-back, so it is reported rather than accepted.
    """
    api_version = version_payload.get("api_version")
    if not isinstance(api_version, str) or not api_version:
        # Positive matching. An engine that omits the field has not told us it is
        # the version this adapter was written against, and "it said nothing" is
        # not "it is the right one".
        return False, ("the engine did not report an api_version, so this adapter's "
                       f"assumptions are unverified (it was checked against {VERIFIED_API_VERSIONS[0]})")
    if not any(api_version.startswith(v) for v in VERIFIED_API_VERSIONS):
        return False, (f"engine reports api_version {api_version!r}, this adapter was verified "
                       f"against {VERIFIED_API_VERSIONS[0]}")
    features = version_payload.get("features")
    if isinstance(features, dict) and features.get("store_document_text") is False:
        return False, "the deployment does not store document text, so document read-back is unavailable"
    return True, None
