# Vendored, unmodified copy of clients/python/model_registry_client.py from the model-registry project
# (https://models.masaru.uk, schema v1, docs/INTEGRATION.md there). Do not edit it here: re-copy the file when
# the registry publishes a new client. The app uses it only through src/core/registry_source.py (Decision 110).
"""model-registry client for Python (>= 3.9, standard library only). Copy this single file into your project.

Schema v1. Docs: docs/INTEGRATION.md.

    from model_registry_client import ModelRegistryClient
    registry = ModelRegistryClient(snapshot="catalog.snapshot.json")      # snapshot = offline fallback
    result = registry.models(provider="google", input="vision", sort="-context_window")
    for m in result["data"]:
        print(m["id"], m["context_window"])

How it works (one code path for network, cache and snapshot): the client downloads /v1/catalog once (conditional GET,
If-None-Match), keeps it for `ttl_seconds` (24 h) and answers every query LOCALLY with the same filter engine as the Worker.
Resolution order: fresh cache -> network (200 / 304) -> stale cache of any age -> bundled snapshot -> raise.
It never raises while a fallback exists; `source` tells where an answer came from. Thread-safe.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

DEFAULT_BASE_URL = "https://models.masaru.uk"
DEFAULT_TTL_SECONDS = 24 * 3600
DEFAULT_TIMEOUT = 8.0
SCHEMA_VERSION = 1
# Cloudflare's Browser Integrity Check rejects urllib's default "Python-urllib/x" User-Agent with HTTP 403 (error 1010), so the
# client always identifies itself. Custom transports may send their own.
USER_AGENT = "model-registry-client-python/0.1"
DEFAULT_CACHE = object()  # sentinel: "<tmpdir>/model-registry-client"; pass cache_dir=None for memory only


class RegistryError(Exception):
    """The request was rejected (bad parameter, unknown provider / model). Same `code`s as the HTTP API."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


class RegistryUnavailableError(Exception):
    """No catalog could be obtained: network failed and there is neither a cache nor a snapshot (or a live-only call failed)."""


# ------------------------------------------------------------------------------- filter engine (port of worker/src/filters.js)

KINDS = ["chat", "embedding", "image_generation", "video_generation", "speech_to_text", "text_to_speech", "music_generation", "realtime", "moderation", "rerank", "other"]
MODALITIES = ["text", "image", "audio", "video", "pdf"]
STATUSES = ["active", "preview", "deprecated"]
LIST_PARAMS = ["provider", "kind", "input", "output", "free", "min_context", "min_output", "tools", "reasoning", "status", "verified", "q", "fields", "sort"]
SORT_FIELDS = ["id", "release_date", "context_window"]
SUMMARY_FIELDS = ["provider", "id", "name", "family", "kind", "input", "output", "context_window", "max_output_tokens", "pricing", "free", "features", "lifecycle", "verified"]
MODEL_FIELDS = SUMMARY_FIELDS + ["aliases", "refs", "sources", "first_seen"]


def _bad(message: str, code: str = "bad_parameter") -> RegistryError:
    return RegistryError(400, code, message)


def _list(raw: str) -> List[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


def _raw_params(query: Dict[str, Any]) -> Dict[str, str]:
    """Query dict -> the raw strings the HTTP API would see (lists joined with commas, bools as true/false)."""
    out: Dict[str, str] = {}
    for k, v in (query or {}).items():
        if v is None:
            continue
        if isinstance(v, bool):
            out[k] = "true" if v else "false"
        elif isinstance(v, (list, tuple)):
            out[k] = ",".join(str(x) for x in v)
        elif isinstance(v, float) and v.is_integer():
            out[k] = str(int(v))  # JavaScript's String(1e5) is "100000": keep the clients identical
        else:
            out[k] = str(v)
    return out


def _parse_query(query, resolve_provider, default_kind, default_status, allowed_fields) -> Dict[str, Any]:
    params = _raw_params(query)
    for key in params:
        if key not in LIST_PARAMS and not key.startswith("_"):
            raise _bad('Unknown parameter "%s". Allowed: %s.' % (key, ", ".join(LIST_PARAMS)))
    f: Dict[str, Any] = {
        "provider": None, "kind": default_kind, "input": [], "output": [], "free": None, "min_context": None, "min_output": None,
        "tools": None, "reasoning": None, "status": default_status, "verified": None, "q": None,
    }
    if "provider" in params:
        items = _list(params["provider"])
        if not items or len(items) > 50:
            raise _bad('Parameter "provider" needs 1-50 comma-separated provider IDs.')
        ids: List[str] = []
        for raw in items:
            pid = resolve_provider(raw)
            if not pid:
                raise _bad('Unknown provider "%s". See /v1/providers.' % raw, "unknown_provider")
            if pid not in ids:
                ids.append(pid)
        f["provider"] = ids
    for name, allowed in (("kind", KINDS), ("status", STATUSES)):
        if name not in params:
            continue
        items = _list(params[name])
        if not items or len(items) > 50:
            raise _bad('Parameter "%s" needs 1-50 comma-separated values.' % name)
        if len(items) == 1 and items[0] == "any":
            f[name] = []
        else:
            unknown = next((v for v in items if v not in allowed), None)
            if unknown is not None:
                raise _bad('Invalid %s "%s". Allowed: %s, or any.' % (name, unknown, ", ".join(allowed)))
            f[name] = list(dict.fromkeys(items))
    for name in ("input", "output"):
        if name not in params:
            continue
        items = [("image" if (name == "input" and v == "vision") else v) for v in _list(params[name])]
        if not items or len(items) > 50:
            raise _bad('Parameter "%s" needs 1-50 comma-separated modalities.' % name)
        unknown = next((v for v in items if v not in MODALITIES), None)
        if unknown is not None:
            raise _bad('Invalid %s modality "%s". Allowed: %s%s.' % (name, unknown, ", ".join(MODALITIES), ", vision (alias of image)" if name == "input" else ""))
        f[name] = list(dict.fromkeys(items))
    for name in ("free", "tools", "reasoning", "verified"):
        if name not in params:
            continue
        v = params[name].strip().lower()
        if v in ("true", "1"):
            f[name] = True
        elif v in ("false", "0"):
            f[name] = False
        else:
            raise _bad('Parameter "%s" must be true or false.' % name)
    for name in ("min_context", "min_output"):
        if name not in params:
            continue
        raw = params[name].strip()
        if not (raw.isascii() and raw.isdigit() and 1 <= len(raw) <= 15):
            raise _bad('Parameter "%s" must be a non-negative integer.' % name)
        f[name] = int(raw)
    if "q" in params:
        t = params["q"].strip()
        if not t or len(t) > 100:
            raise _bad('Parameter "q" must be 1-100 characters.')
        f["q"] = t.lower()
    fields = None
    if "fields" in params:
        items = _list(params["fields"])
        if not items:
            raise _bad('Parameter "fields" is empty.')
        unknown = next((v for v in items if v not in allowed_fields), None)
        if unknown is not None:
            raise _bad('Unknown field "%s". Allowed: %s.' % (unknown, ", ".join(allowed_fields)))
        fields = list(dict.fromkeys(["provider", "id"] + items))
    sort = None
    if "sort" in params:
        raw = params["sort"]
        desc = raw.startswith("-")
        field = raw[1:] if desc else raw
        if field not in SORT_FIELDS:
            raise _bad('Invalid sort "%s". Allowed: %s (prefix "-" for descending).' % (raw, ", ".join(SORT_FIELDS)))
        sort = (field, desc)

    echo: Dict[str, Any] = {}
    if f["provider"]:
        echo["provider"] = f["provider"]
    echo["kind"] = f["kind"] if f["kind"] else "any"
    if f["input"]:
        echo["input"] = f["input"]
    if f["output"]:
        echo["output"] = f["output"]
    for k in ("free", "min_context", "min_output", "tools", "reasoning"):
        if f[k] is not None:
            echo[k] = f[k]
    echo["status"] = f["status"] if f["status"] else "any"
    if f["verified"] is not None:
        echo["verified"] = f["verified"]
    if f["q"]:
        echo["q"] = f["q"]
    if fields:
        echo["fields"] = fields
    if sort:
        echo["sort"] = ("-" if sort[1] else "") + sort[0]
    return {"filters": f, "fields": fields, "sort": sort, "echo": echo}


def _matches(m: Dict[str, Any], f: Dict[str, Any]) -> bool:
    if f["provider"] and m["provider"] not in f["provider"]:
        return False
    if f["kind"] and m["kind"] not in f["kind"]:
        return False
    if f["input"] and not all(x in m["input"] for x in f["input"]):
        return False
    if f["output"] and not all(x in m["output"] for x in f["output"]):
        return False
    if f["free"] is not None and m["free"] is not f["free"]:
        return False
    if f["min_context"] is not None and not (m["context_window"] is not None and m["context_window"] >= f["min_context"]):
        return False
    if f["min_output"] is not None and not (m["max_output_tokens"] is not None and m["max_output_tokens"] >= f["min_output"]):
        return False
    if f["tools"] is not None and m["features"].get("tools") is not f["tools"]:
        return False
    if f["reasoning"] is not None and m["features"].get("reasoning") is not f["reasoning"]:
        return False
    if f["status"] and m["lifecycle"]["status"] not in f["status"]:
        return False
    if f["verified"] is not None and m["verified"] is not f["verified"]:
        return False
    if f["q"]:
        name = m.get("name")
        if not (f["q"] in m["id"].lower() or f["q"] in ("null" if name is None else str(name)).lower()):
            return False
    return True


def _sort_models(items: List[Dict[str, Any]], sort) -> List[Dict[str, Any]]:
    if not sort:
        return items
    field, desc = sort

    def value(m):
        if field == "id":
            return m["id"]
        if field == "release_date":
            return m["lifecycle"]["release_date"]
        return m["context_window"]

    present = [m for m in items if value(m) is not None]
    missing = [m for m in items if value(m) is None]  # nulls last in BOTH directions
    # ties by provider then id, ascending, in both directions
    present.sort(key=lambda m: (m["provider"], m["id"]))
    present.sort(key=value, reverse=desc)  # stable: the tie order above is kept
    missing.sort(key=lambda m: (m["provider"], m["id"]))
    return present + missing


def _project(items: List[Dict[str, Any]], fields) -> List[Dict[str, Any]]:
    if not fields:
        return items
    return [{k: m[k] for k in fields if k in m} for m in items]


def to_summary(m: Dict[str, Any]) -> Dict[str, Any]:
    """The list-item projection of a full model (`GET /v1/models` returns these)."""
    lc = m["lifecycle"]
    pr = m.get("pricing")
    return {
        "provider": m["provider"], "id": m["id"], "name": m["name"], "family": m["family"], "kind": m["kind"], "input": m["input"],
        "output": m["output"], "context_window": m["context_window"], "max_output_tokens": m["max_output_tokens"],
        "pricing": {"input": pr["input"], "output": pr["output"]} if pr else None, "free": m["free"], "features": m["features"],
        "lifecycle": {"status": lc["status"], "release_date": lc["release_date"], "deprecation_date": lc["deprecation_date"], "retirement_date": lc["retirement_date"]},
        "verified": m["verified"],
    }


def _resolver(providers: Dict[str, Any]) -> Callable[[str], Optional[str]]:
    mapping: Dict[str, str] = {}
    for p in providers.values():
        mapping[p["id"].lower()] = p["id"]
        for a in p.get("aliases") or []:
            mapping[str(a).lower()] = p["id"]
    return lambda raw: mapping.get(str(raw).strip().lower())


def query_catalog(catalog: Dict[str, Any], kind: str, query: Optional[Dict[str, Any]] = None, provider: Optional[str] = None) -> Dict[str, Any]:
    """Answer a query from a catalog dict (the `/v1/catalog` or `data/catalog.json` shape): {"data", "count", "query"}.

    `kind` is "models" (summaries, defaults kind=chat status=active,preview) or "providerModels" (full models, no defaults).
    """
    resolve = _resolver(catalog["providers"])
    if kind == "models":
        p = _parse_query(query, resolve, ["chat"], ["active", "preview"], SUMMARY_FIELDS)
        summaries = [s for s in (to_summary(m) for m in catalog["models"]) if _matches(s, p["filters"])]
        data = _project(_sort_models(summaries, p["sort"]), p["fields"])
        return {"data": data, "count": len(data), "query": p["echo"]}
    pid = resolve(provider)
    if not pid:
        raise RegistryError(404, "unknown_provider", 'Unknown provider "%s". See /v1/providers.' % provider)
    p = _parse_query(query, resolve, [], [], MODEL_FIELDS)
    if p["filters"]["provider"] and not (len(p["filters"]["provider"]) == 1 and p["filters"]["provider"][0] == pid):
        raise _bad('Parameter "provider" conflicts with the provider in the path.')
    own = [m for m in catalog["models"] if m["provider"] == pid and _matches(m, p["filters"])]
    data = _project(_sort_models(own, p["sort"]), p["fields"])
    echo = dict(p["echo"])
    echo["provider"] = [pid]
    return {"data": data, "count": len(data), "query": echo}


# ----------------------------------------------------------------------------------------------------------------- client


def _valid_catalog(c: Any) -> bool:
    return isinstance(c, dict) and c.get("schema_version") == SCHEMA_VERSION and isinstance(c.get("models"), list) and isinstance(c.get("providers"), dict)


class _MemoryStore:
    def __init__(self) -> None:
        self._text: Optional[str] = None

    def read(self) -> Optional[str]:
        return self._text

    def write(self, text: str) -> None:
        self._text = text


class _FileStore:
    def __init__(self, directory: str) -> None:
        self._dir = directory
        self._file = os.path.join(directory, "catalog-v1.json")

    def read(self) -> Optional[str]:
        try:
            with open(self._file, "r", encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return None

    def write(self, text: str) -> None:
        os.makedirs(self._dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, self._file)  # atomic: a reader never sees a half-written cache
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


Transport = Callable[[str, Dict[str, str], float], Tuple[int, Dict[str, str], bytes]]


def _urllib_transport(url: str, headers: Dict[str, str], timeout: float) -> Tuple[int, Dict[str, str], bytes]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec: fixed https base URL
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as e:  # urllib raises for 304 and 4xx/5xx: turn them into plain responses
        body = b""
        try:
            body = e.read()
        except Exception:  # noqa: BLE001
            pass
        return e.code, {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}, body


class ModelRegistryClient:
    """See the module docstring. All methods are synchronous and thread-safe."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        timeout: float = DEFAULT_TIMEOUT,
        cache_dir: Any = DEFAULT_CACHE,
        snapshot: Any = None,
        transport: Optional[Transport] = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        """
        :param cache_dir: directory for the on-disk cache; None = memory only; an object with read()/write(text) = custom store.
        :param snapshot: bundled catalog (dict, or a path to a JSON file): the last-resort fallback.
        :param transport: callable(url, headers, timeout) -> (status, headers_lowercase, body_bytes). Default: urllib.
        """
        self.base_url = base_url.rstrip("/")
        self.ttl_seconds = ttl_seconds
        self.timeout = timeout
        self._now = now
        self._transport = transport or _urllib_transport
        if cache_dir is None:
            self._store: Any = _MemoryStore()
        elif cache_dir is DEFAULT_CACHE:
            self._store = _FileStore(os.path.join(tempfile.gettempdir(), "model-registry-client"))
        elif hasattr(cache_dir, "read") and hasattr(cache_dir, "write"):
            self._store = cache_dir
        else:
            self._store = _FileStore(str(cache_dir))
        self._snapshot_opt = snapshot
        self._snapshot: Any = False  # False = not loaded yet
        self._entry: Any = False
        self._lock = threading.RLock()

    # -- internals ------------------------------------------------------------------------------------------------

    def _load_entry(self) -> Optional[Dict[str, Any]]:
        if self._entry is not False:
            return self._entry
        self._entry = None
        try:
            text = self._store.read()
            e = json.loads(text) if text else None
            if isinstance(e, dict) and _valid_catalog(e.get("catalog")) and isinstance(e.get("fetched_at"), (int, float)):
                self._entry = e
        except Exception:  # noqa: BLE001 - an unreadable cache is no cache
            pass
        return self._entry

    def _load_snapshot(self) -> Optional[Dict[str, Any]]:
        if self._snapshot is not False:
            return self._snapshot
        s = self._snapshot_opt
        try:
            if isinstance(s, (str, os.PathLike)):
                with open(s, "r", encoding="utf-8") as fh:
                    s = json.load(fh)
        except Exception:  # noqa: BLE001
            s = None
        self._snapshot = s if _valid_catalog(s) else None
        return self._snapshot

    def _persist(self, entry: Dict[str, Any], warnings: List[str]) -> None:
        try:
            self._store.write(json.dumps(entry))
        except Exception as e:  # noqa: BLE001 - a read-only disk must not break the app
            warnings.append("cache write failed: %s" % e)

    def _http(self, path: str, headers: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, str], bytes]:
        h = {"accept": "application/json", "user-agent": USER_AGENT}
        h.update(headers or {})
        return self._transport(self.base_url + path, h, self.timeout)

    # -- catalog --------------------------------------------------------------------------------------------------

    def catalog(self, force_refresh: bool = False) -> Dict[str, Any]:
        """The catalog and where it came from: {"catalog", "source", "stale", "build_id", "generated_at", "fetched_at",
        "age_seconds", "not_modified", "warnings"}. `source` is cache | network | stale-cache | snapshot."""
        with self._lock:
            now = self._now()
            warnings: List[str] = []
            entry = self._load_entry()

            def result(e: Dict[str, Any], source: str, not_modified: bool = False) -> Dict[str, Any]:
                fetched = e.get("fetched_at")
                build = e["catalog"].get("build") or {}
                return {
                    "catalog": e["catalog"], "source": source, "stale": source in ("stale-cache", "snapshot"), "build_id": build.get("id"),
                    "generated_at": build.get("generated_at"), "fetched_at": fetched,
                    "age_seconds": max(0, int(round(now - fetched))) if fetched else None, "not_modified": not_modified, "warnings": warnings,
                }

            if entry and not force_refresh and now - entry["fetched_at"] < self.ttl_seconds:
                return result(entry, "cache")
            try:
                headers = {"if-none-match": entry["etag"]} if entry and entry.get("etag") else {}
                status, resp_headers, body = self._http("/v1/catalog", headers)
                if status == 304 and entry:
                    entry["fetched_at"] = now
                    self._persist(entry, warnings)
                    return result(entry, "network", True)
                if status != 200:
                    raise RuntimeError("HTTP %d" % status)
                catalog = json.loads(body.decode("utf-8"))
                if not _valid_catalog(catalog):
                    raise RuntimeError("unsupported or malformed catalog (schema_version %s)" % (catalog.get("schema_version") if isinstance(catalog, dict) else None))
                fresh = {"fetched_at": now, "etag": resp_headers.get("etag"), "catalog": catalog}  # ETag stored exactly as received (may be weak)
                self._entry = fresh
                self._persist(fresh, warnings)
                return result(fresh, "network")
            except Exception as e:  # noqa: BLE001 - every failure falls through the fallback chain
                warnings.append("network: %s" % e)
                if entry:
                    return result(entry, "stale-cache")
                snap = self._load_snapshot()
                if snap:
                    return result({"catalog": snap, "fetched_at": None}, "snapshot")
                raise RegistryUnavailableError("No catalog available (%s) and no cache or snapshot is configured." % "; ".join(warnings)) from e

    def _envelope(self, c: Dict[str, Any], r: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "build_id": c["build_id"], "generated_at": c["generated_at"], "count": r["count"], "query": r["query"],
            "data": r["data"], "source": c["source"], "stale": c["stale"], "age_seconds": c["age_seconds"], "warnings": c["warnings"],
        }

    @staticmethod
    def _merge(query: Optional[Dict[str, Any]], kw: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(query or {})
        merged.update(kw)
        return merged

    def models(self, query: Optional[Dict[str, Any]] = None, force_refresh: bool = False, **params: Any) -> Dict[str, Any]:
        """Filtered model SUMMARIES (same semantics and defaults as GET /v1/models: kind=chat, status=active,preview).
        Parameters as keyword arguments or a dict: models(provider="google", input="vision", free=True)."""
        c = self.catalog(force_refresh)
        return self._envelope(c, query_catalog(c["catalog"], "models", self._merge(query, params)))

    def provider_models(self, provider: str, query: Optional[Dict[str, Any]] = None, force_refresh: bool = False, **params: Any) -> Dict[str, Any]:
        """ALL full models of one provider (no default filters, like GET /v1/models/{provider})."""
        c = self.catalog(force_refresh)
        return self._envelope(c, query_catalog(c["catalog"], "providerModels", self._merge(query, params), provider))

    def model(self, provider: str, model_id: str, force_refresh: bool = False) -> Dict[str, Any]:
        """One full model by id (or alias). Raises RegistryError(404) when unknown."""
        c = self.catalog(force_refresh)
        pid = _resolver(c["catalog"]["providers"])(provider)
        if not pid:
            raise RegistryError(404, "unknown_provider", 'Unknown provider "%s". See /v1/providers.' % provider)
        own = [m for m in c["catalog"]["models"] if m["provider"] == pid]
        hit = next((m for m in own if m["id"] == model_id), None) or next((m for m in own if model_id in (m.get("aliases") or [])), None)
        if hit is None:
            raise RegistryError(404, "not_found", 'Model "%s" not found for provider "%s".' % (model_id, pid))
        return {
            "schema_version": SCHEMA_VERSION, "build_id": c["build_id"], "generated_at": c["generated_at"], "data": hit, "source": c["source"],
            "stale": c["stale"], "age_seconds": c["age_seconds"], "warnings": c["warnings"],
        }

    def providers(self, force_refresh: bool = False) -> Dict[str, Any]:
        """The provider registry (base URLs, key URLs, key prefixes, free tier...), sorted by id."""
        c = self.catalog(force_refresh)
        data = sorted(c["catalog"]["providers"].values(), key=lambda p: p["id"])
        return self._envelope(c, {"data": data, "count": len(data), "query": {}})

    # -- live-only calls (never answered from cache) --------------------------------------------------------------------

    def _live(self, path: str) -> Dict[str, Any]:
        try:
            status, _headers, body = self._http(path)
        except Exception as e:  # noqa: BLE001
            raise RegistryUnavailableError("GET %s failed: %s" % (path, e)) from e
        try:
            parsed = json.loads(body.decode("utf-8")) if body else None
        except ValueError:
            parsed = None
        if not 200 <= status < 300:
            err = (parsed or {}).get("error", {}) if isinstance(parsed, dict) else {}
            raise RegistryError(status, err.get("code", "http_%d" % status), err.get("message", "HTTP %d" % status))
        return parsed

    def health(self) -> Dict[str, Any]:
        """Live build age and per-provider sync status."""
        return self._live("/v1/health")

    def changes(self, since: Optional[str] = None, provider: Optional[str] = None) -> Dict[str, Any]:
        """Live change feed: changes(since="2026-11-01", provider="google")."""
        from urllib.parse import urlencode

        q = {k: v for k, v in (("since", since), ("provider", provider)) if v}
        return self._live("/v1/changes" + ("?" + urlencode(q) if q else ""))
