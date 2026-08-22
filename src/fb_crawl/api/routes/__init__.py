"""HTTP route builders."""

from fb_crawl.api.routes.health import REQUIRED_MIGRATION, create_health_router

__all__ = ["REQUIRED_MIGRATION", "create_health_router"]
