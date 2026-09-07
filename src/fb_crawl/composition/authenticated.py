"""Authenticated crawl runtime assembly without import-time browser dependencies."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, ContextManager, Protocol

from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import ConfigurationError, SessionError
from fb_crawl.core.models import ScrapeRequest, ScrapeResult, UserRecord
from fb_crawl.services.checkpoint import CheckpointingService
from fb_crawl.services.execution_control import (
    ExecutionControl,
    NavigationPacer,
    NOOP_EXECUTION_CONTROL,
    NOOP_NAVIGATION_PACER,
)

if TYPE_CHECKING:
    from fb_data_pipeline.config import PipelineSettings
    from fb_data_pipeline.services.ingestion import IngestionReport


@dataclass(frozen=True, slots=True)
class AuthenticatedComponents:
    create_browser: Callable[[BrowserSettings], object]
    create_service: Callable[[], CheckpointingService]


@dataclass(frozen=True, slots=True)
class AuthenticatedPersistenceRuntime:
    ingest_result: Callable[[ScrapeResult[UserRecord]], IngestionReport]
    close: Callable[[], None]


class AuthenticatedJobSession(Protocol):
    def validate(self, request: ScrapeRequest) -> None: ...

    def run(self, request: ScrapeRequest) -> ScrapeResult[UserRecord]: ...

    def ingest(self, result: ScrapeResult[UserRecord]) -> IngestionReport: ...


class AuthenticatedCleanupError(RuntimeError):
    """Sanitized cleanup failure labelled only by the affected resource."""

    def __init__(self, resource: str) -> None:
        if resource not in {"provider", "browser"}:
            raise ValueError("Unsupported authenticated cleanup resource.")
        self.resource = resource
        super().__init__(f"Authenticated {resource} cleanup failed.")


def saved_session_only_credentials() -> tuple[str, str]:
    """Reject worker login attempts: workers may only restore saved sessions."""
    raise SessionError("Saved Facebook session requires manual login.")


def _build_authenticated_components(
    settings: BrowserSettings,
    credentials_provider: Callable[[], tuple[str, str]],
    *,
    control: ExecutionControl = NOOP_EXECUTION_CONTROL,
    navigation_pacer: NavigationPacer = NOOP_NAVIGATION_PACER,
) -> AuthenticatedComponents:
    """Construct browser adapters only when an authenticated runtime is needed."""
    try:
        from fb_crawl.adapters.browser.comments import CommentsCollector
        from fb_crawl.adapters.browser.driver import create_browser
        from fb_crawl.adapters.browser.inspect import BrowserInspector
        from fb_crawl.adapters.browser.login import SessionManager
        from fb_crawl.adapters.browser.members import MembersCollector
        from fb_crawl.adapters.browser.message_parser import MessageParser
        from fb_crawl.adapters.browser.messages import MessagesCollector
        from fb_crawl.adapters.browser.profile_parser import ProfileParser
        from fb_crawl.adapters.browser.profile_uid import ProfileUidResolver
        from fb_crawl.adapters.browser.reaction_parser import ReactionParser
        from fb_crawl.adapters.browser.reactions import ReactionsCollector
        from fb_crawl.adapters.browser.relationships import RelationshipCollector
        from fb_crawl.adapters.browser.profiles import ProfileEnricher
        from fb_crawl.adapters.browser.session import SessionStore
        from fb_crawl.adapters.browser.user_parser import UserParser
        from fb_crawl.services.authenticated import AuthenticatedService
        from fb_crawl.services.uid_cache import (
            CachedProfileUidResolver,
            JsonProfileUidCache,
        )
    except ModuleNotFoundError as error:
        dependency = str(error.name)
        if dependency == "selenium" or dependency.startswith("selenium."):
            raise ConfigurationError(
                "Authenticated mode requires: "
                'python -m pip install -e ".[browser]"'
            ) from error
        if dependency == "bs4" or dependency.startswith("bs4."):
            raise ConfigurationError(
                "Authenticated mode requires: "
                'python -m pip install -e ".[browser]"'
            ) from error
        raise

    def create_service() -> CheckpointingService:
        service = AuthenticatedService(
            SessionManager(
                SessionStore(
                    settings.session_path,
                    control=control,
                    navigation_pacer=navigation_pacer,
                ),
                settings,
                credentials_provider,
                control=control,
                navigation_pacer=navigation_pacer,
            ),
            MembersCollector(
                settings,
                control=control,
                navigation_pacer=navigation_pacer,
            ),
            CommentsCollector(
                settings,
                control=control,
                navigation_pacer=navigation_pacer,
            ),
            UserParser(),
            ProfileEnricher(
                settings,
                ProfileParser(),
                control=control,
                navigation_pacer=navigation_pacer,
            ),
            relationships=RelationshipCollector(
                settings,
                control=control,
                navigation_pacer=navigation_pacer,
            ),
            reactions=ReactionsCollector(
                settings,
                control=control,
                navigation_pacer=navigation_pacer,
            ),
            relationship_parser=UserParser(allow_plain_profile_links=True),
            reaction_parser=ReactionParser(),
            uid_resolver=CachedProfileUidResolver(
                ProfileUidResolver(
                    settings,
                    control=control,
                    navigation_pacer=navigation_pacer,
                ),
                JsonProfileUidCache("runtime/cache/profile-uids.json"),
            ),
            messages=MessagesCollector(settings),
            message_parser=MessageParser(),
            inspector=BrowserInspector(settings),
            control=control,
        )
        return CheckpointingService(service)

    return AuthenticatedComponents(
        create_browser=create_browser,
        create_service=create_service,
    )


def build_authenticated_components(
    settings: BrowserSettings,
    credentials_provider: Callable[[], tuple[str, str]],
    *,
    control: ExecutionControl = NOOP_EXECUTION_CONTROL,
) -> AuthenticatedComponents:
    return _build_authenticated_components(
        settings,
        credentials_provider,
        control=control,
    )


def build_authenticated_persistence(
    pipeline_settings: PipelineSettings,
) -> AuthenticatedPersistenceRuntime:
    from fb_data_pipeline.exceptions import PipelineExecutionError
    from fb_data_pipeline.providers.fbnumber import FBNumberProvider
    from fb_data_pipeline.repositories.postgres import PostgresRepository
    from fb_data_pipeline.services.ingestion import AuthenticatedIngestionService
    from fb_data_pipeline.services.persistence import PipelinePersistenceService
    from fb_data_pipeline.services.pipeline import EnrichmentPipeline
    from fb_crawl.providers.health import (
        ObservedPhoneProvider,
        PostgresProviderHealthRepository,
    )

    try:
        pipeline_settings.require_database()
        pipeline_settings.require_fb_number()
    except ConfigurationError as error:
        raise PipelineExecutionError(
            "Persistence pipeline configuration is incomplete."
        ) from error

    provider = ObservedPhoneProvider(
        FBNumberProvider.from_settings(pipeline_settings),
        PostgresProviderHealthRepository(
            pipeline_settings.database_url,
            statement_timeout_seconds=(
                pipeline_settings.database_statement_timeout_seconds
            ),
        ),
        configured=True,
    )
    repository = PostgresRepository(
        pipeline_settings.database_url,
        statement_timeout_seconds=(
            pipeline_settings.database_statement_timeout_seconds
        ),
    )
    ingestion = AuthenticatedIngestionService(
        EnrichmentPipeline(provider),
        PipelinePersistenceService(repository),
    )
    return AuthenticatedPersistenceRuntime(
        ingest_result=lambda result: ingestion.ingest(
            result,
            default_country_code=pipeline_settings.default_country_code,
        ),
        close=provider.close,
    )


@dataclass(slots=True)
class _AuthenticatedJobSession:
    service: CheckpointingService
    browser: object
    persistence: AuthenticatedPersistenceRuntime

    def validate(self, request: ScrapeRequest) -> None:
        self.service.validate(request)

    def run(self, request: ScrapeRequest) -> ScrapeResult[UserRecord]:
        return self.service.run(request, self.browser)

    def ingest(self, result: ScrapeResult[UserRecord]) -> IngestionReport:
        return self.persistence.ingest_result(result)


def _close_authenticated_runtime(
    persistence: AuthenticatedPersistenceRuntime | None,
    browser: object | None,
) -> tuple[AuthenticatedCleanupError, ...]:
    failures: list[AuthenticatedCleanupError] = []
    if persistence is not None:
        try:
            persistence.close()
        except Exception:
            failures.append(AuthenticatedCleanupError("provider"))
    if browser is not None:
        try:
            browser.quit()
        except Exception:
            failures.append(AuthenticatedCleanupError("browser"))
    return tuple(failures)


def _preserve_primary_cleanup_failures(
    primary: BaseException,
    failures: tuple[AuthenticatedCleanupError, ...],
) -> None:
    if not failures:
        return
    cleanup_group = ExceptionGroup(
        "Authenticated job cleanup failed.",
        failures,
    )
    try:
        primary.cleanup_failures = cleanup_group
    except (AttributeError, TypeError):
        primary.add_note("Authenticated job cleanup failed.")


@contextmanager
def open_authenticated_job_session(
    browser_settings: BrowserSettings,
    pipeline_settings: PipelineSettings,
    control: ExecutionControl,
    navigation_pacer: NavigationPacer,
) -> ContextManager[AuthenticatedJobSession]:
    """Open one worker runtime and close its provider and browser independently."""
    browser: object | None = None
    persistence: AuthenticatedPersistenceRuntime | None = None
    primary: BaseException | None = None
    try:
        components = _build_authenticated_components(
            browser_settings,
            saved_session_only_credentials,
            control=control,
            navigation_pacer=navigation_pacer,
        )
        service = components.create_service()
        browser = components.create_browser(browser_settings)
        persistence = build_authenticated_persistence(pipeline_settings)
        yield _AuthenticatedJobSession(service, browser, persistence)
    except BaseException as error:
        primary = error
        raise
    finally:
        failures = _close_authenticated_runtime(persistence, browser)
        if primary is not None:
            _preserve_primary_cleanup_failures(primary, failures)
        elif len(failures) == 1:
            raise failures[0]
        elif failures:
            raise ExceptionGroup(
                "Authenticated job cleanup failed.",
                failures,
            )
