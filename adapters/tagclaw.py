"""
adapters/tagclaw.py — TagClaw platform adapter.

Implements AbstractPlatformAdapter for the TagClaw API.
All endpoints come from https://tagclaw.com/SKILL.md

Dependencies: stdlib only (urllib, json, pathlib, subprocess).
No third-party packages required.
"""

import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent urllib from silently following redirects on POST.

    By default ``urllib`` converts POST → GET on 301/302, which drops the
    request body — the root cause of the "Content cannot be empty" bug.
    Raising on redirect lets the caller surface the real issue (wrong URL)
    instead of silently losing data.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        if req.get_method() in ("POST", "PUT", "PATCH"):
            raise urllib.error.HTTPError(
                newurl, code, f"Redirect {code} on {req.get_method()} to {newurl}", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_NoRedirectHandler)

from adapters.base import AbstractPlatformAdapter

BASE_URL = "https://bsc-api.tagai.fun/tagclaw"
DEFAULT_IDENTITY_PATH = Path(__file__).parent.parent / "config" / "agency-identity.json"


def extract_me_agent(body: Any) -> dict[str, Any]:
    """Normalize a ``/me`` response into the inner agent dict.

    The TagClaw server has shipped three shapes over time:
      - ``{"success": true, "agent": {...}}``        — current (2026-04)
      - ``{"success": true, "data": {"agent": {...}}}`` — older nested wrapper
      - ``{"success": true, "data": {...flat fields...}}`` — earlier shape
      - ``{...flat fields...}``                        — legacy bare dict

    Callers used to branch ad-hoc, which is how ``ownerTwitterHandle`` went
    missing on the latest shape. Centralize the unwrap so every reader sees
    the same agent dict without re-implementing the precedence.
    """
    if not isinstance(body, dict):
        return {}
    agent = body.get("agent")
    if isinstance(agent, dict):
        return agent
    data = body.get("data")
    if isinstance(data, dict):
        nested = data.get("agent")
        if isinstance(nested, dict):
            return nested
        return data
    return body


class TagClawAdapter(AbstractPlatformAdapter):
    """
    TagClaw API adapter.

    Authentication reads the workspace-local skills/tagclaw/.env API key.
    The credential path is read at construction time but never logged or stored
    in any output.
    """

    def __init__(
        self,
        identity_path: Path | None = None,
        base_url: str = BASE_URL,
        timeout: int = 15,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._api_key: str | None = None

        identity_path = identity_path or DEFAULT_IDENTITY_PATH
        self._load_credentials(identity_path)

    def _load_credentials(self, identity_path: Path) -> None:
        """Load API key from workspace skills/tagclaw/.env."""
        try:
            workspace = Path(os.environ.get("OPENCLAW_WORKSPACE") or (identity_path.parent.parent))
            skill_env = workspace / "skills" / "tagclaw" / ".env"
            if not skill_env.exists():
                return
            data: dict[str, str] = {}
            for line in skill_env.read_text().splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                v = v.strip()
                if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
                    v = v[1:-1]
                data[k.strip()] = v
            self._api_key = data.get("TAGCLAW_API_KEY")
        except Exception:
            # Credentials loading is best-effort — adapter still works for public endpoints
            pass

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an authenticated HTTP request to the TagClaw API."""
        # Ensure path ends with "/" to avoid 301 redirects that drop POST body.
        if "?" not in path:
            path = path.rstrip("/") + "/"
        else:
            base, qs = path.split("?", 1)
            path = base.rstrip("/") + "/?" + qs

        url = f"{self._base_url}{path}"
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        data: bytes | None = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with _opener.open(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw_body = e.read().decode("utf-8", errors="replace")
            try:
                err_detail = json.loads(raw_body)
            except Exception:
                err_detail = {"raw": raw_body}
            raise TagClawAPIError(
                status=e.code,
                message=f"{method} {path} → {e.code}",
                detail=err_detail,
            ) from e
        except urllib.error.URLError as e:
            raise TagClawAPIError(
                status=0,
                message=f"Network error: {e.reason}",
                detail={},
            ) from e

    # ── AbstractPlatformAdapter implementation ─────────────────────────────

    # Default community tick for posts.  The TagClaw API requires a ``tick``
    # field; ``IPShare`` is the canonical self-IP community.
    DEFAULT_TICK = "IPShare"

    def post(self, text: str, tick: str | None = None) -> dict[str, Any]:
        """Create a new post. ``tick`` defaults to ``IPShare``.

        The TagClaw API payload contract uses ``text`` (not ``content``) and
        requires a ``tick`` field identifying the target community.
        """
        body: dict[str, Any] = {
            "text": text,
            "tick": tick or self.DEFAULT_TICK,
        }
        return self._request("POST", "/post", body)

    def reply(self, tweet_id: str, text: str) -> dict[str, Any]:
        """Reply to a post."""
        return self._request("POST", "/reply", {
            "postId": tweet_id,
            "text": text,
        })

    def like(self, tweet_id: str) -> dict[str, Any]:
        """Like a post."""
        return self._request("POST", "/like", {"postId": tweet_id})

    def curate(self, tweet_id: str, vp: int) -> dict[str, Any]:
        """Curate a post with VP."""
        if vp <= 0:
            raise ValueError(f"VP must be positive, got {vp}")
        return self._request("POST", "/curate", {
            "postId": tweet_id,
            "vp": vp,
        })

    def get_feed(self, page: int = 1) -> list[dict[str, Any]]:
        """Fetch the social feed."""
        result = self._request("GET", f"/feed?page={page}")
        # Normalize: the API may return {"posts": [...]} or a bare list
        if isinstance(result, list):
            return result
        return result.get("tweets") or result.get("posts") or result.get("data") or []

    def get_me(self) -> dict[str, Any]:
        """Get authenticated agent profile.

        Returns the **agent dict** directly, regardless of which envelope the
        server uses. See ``extract_me_agent`` for the precedence rules.
        """
        return extract_me_agent(self._request("GET", "/me"))

    def get_me_raw(self) -> dict[str, Any]:
        """Get the raw ``/me`` response untouched (for callers that need
        success flags or sibling fields next to ``agent``)."""
        return self._request("GET", "/me")

    def get_trending_ticks(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get trending token tickers."""
        result = self._request("GET", f"/trending?limit={limit}")
        if isinstance(result, list):
            return result
        return result.get("ticks") or result.get("data") or []


class TagClawAPIError(Exception):
    """Raised when the TagClaw API returns an error response."""

    def __init__(self, status: int, message: str, detail: dict[str, Any]) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail

    def __repr__(self) -> str:
        return f"TagClawAPIError(status={self.status}, message={self!s})"
