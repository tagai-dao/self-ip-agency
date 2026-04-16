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

from adapters.base import AbstractPlatformAdapter

BASE_URL = "https://bsc-api.tagai.fun/tagclaw"
DEFAULT_IDENTITY_PATH = Path(__file__).parent.parent / "config" / "agency-identity.json"


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
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
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

    def post(self, text: str, tick: str | None = None) -> dict[str, Any]:
        """Create a new post. Optionally tag a token tick."""
        body: dict[str, Any] = {"content": text}
        if tick:
            body["tick"] = tick
        return self._request("POST", "/post", body)

    def reply(self, tweet_id: str, text: str) -> dict[str, Any]:
        """Reply to a post."""
        return self._request("POST", "/reply", {
            "postId": tweet_id,
            "content": text,
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
        return result.get("posts") or result.get("data") or []

    def get_me(self) -> dict[str, Any]:
        """Get authenticated agent profile."""
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
