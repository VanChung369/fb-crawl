"""Re-export SessionPool from core for browser adapter compatibility."""

from fb_crawl.core.session_pool import (
    ManagedSession,
    SessionPool,
    SessionStatus,
)

__all__ = ["ManagedSession", "SessionPool", "SessionStatus"]
