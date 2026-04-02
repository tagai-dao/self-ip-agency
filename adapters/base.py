"""
adapters/base.py — Abstract base class for platform adapters.

All platform integrations (TagClaw, etc.) must implement this interface.
Only stdlib dependencies: urllib, subprocess, json, pathlib.
"""

from abc import ABC, abstractmethod
from typing import Any


class AbstractPlatformAdapter(ABC):
    """
    Abstract base for social/trading platform adapters.

    Implementations provide the mechanics of API calls.
    Agents call these methods directly — no platform-specific logic in agent code.
    """

    @abstractmethod
    def post(self, text: str, tick: str | None = None) -> dict[str, Any]:
        """
        Create a new post on the platform.

        Args:
            text: Post body text (max length is platform-specific).
            tick: Optional token ticker to tag (e.g. "BTC", "ETH").

        Returns:
            dict with at minimum: {"post_id": str, "url": str}
        """
        ...

    @abstractmethod
    def reply(self, tweet_id: str, text: str) -> dict[str, Any]:
        """
        Reply to an existing post.

        Args:
            tweet_id: ID of the post to reply to.
            text: Reply body text.

        Returns:
            dict with at minimum: {"post_id": str, "parent_id": str}
        """
        ...

    @abstractmethod
    def like(self, tweet_id: str) -> dict[str, Any]:
        """
        Like (upvote) a post.

        Args:
            tweet_id: ID of the post to like.

        Returns:
            dict with at minimum: {"success": bool, "post_id": str}
        """
        ...

    @abstractmethod
    def curate(self, tweet_id: str, vp: int) -> dict[str, Any]:
        """
        Curate a post with voting power (stake VP on it).

        Args:
            tweet_id: ID of the post to curate.
            vp: Voting power units to spend on curation.

        Returns:
            dict with at minimum: {"success": bool, "vp_spent": int, "post_id": str}
        """
        ...

    @abstractmethod
    def get_feed(self, page: int = 1) -> list[dict[str, Any]]:
        """
        Fetch the social feed.

        Args:
            page: Pagination page number (1-indexed).

        Returns:
            List of post dicts, each with at minimum:
            {"post_id": str, "author": str, "text": str, "vp_total": int, "created_at": str}
        """
        ...

    @abstractmethod
    def get_me(self) -> dict[str, Any]:
        """
        Fetch the authenticated agent's profile.

        Returns:
            dict with at minimum:
            {"username": str, "eth_addr": str, "vp_balance": int, "op_score": float}
        """
        ...

    @abstractmethod
    def get_trending_ticks(self, limit: int = 10) -> list[dict[str, Any]]:
        """
        Fetch trending token ticks on the platform.

        Args:
            limit: Maximum number of tickers to return.

        Returns:
            List of ticker dicts, each with at minimum:
            {"tick": str, "score": float, "mentions": int, "sentiment": float}
        """
        ...
