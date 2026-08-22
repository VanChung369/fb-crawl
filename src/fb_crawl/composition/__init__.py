"""Lazy application composition for authenticated crawl entrypoints."""

from fb_crawl.composition.authenticated import (
    AuthenticatedCleanupError,
    AuthenticatedComponents,
    AuthenticatedJobSession,
    AuthenticatedPersistenceRuntime,
    build_authenticated_components,
    build_authenticated_persistence,
    open_authenticated_job_session,
    saved_session_only_credentials,
)

__all__ = [
    "AuthenticatedCleanupError",
    "AuthenticatedComponents",
    "AuthenticatedJobSession",
    "AuthenticatedPersistenceRuntime",
    "build_authenticated_components",
    "build_authenticated_persistence",
    "open_authenticated_job_session",
    "saved_session_only_credentials",
]
