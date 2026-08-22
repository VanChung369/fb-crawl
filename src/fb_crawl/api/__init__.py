"""FastAPI application surface for durable crawl jobs."""

from fb_crawl.api.config import ApiSettings, load_api_settings

__all__ = ["ApiSettings", "load_api_settings"]
