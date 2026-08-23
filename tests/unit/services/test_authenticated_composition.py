from __future__ import annotations

import subprocess
import sys
import traceback

import pytest

import fb_crawl.composition.authenticated as composition
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import SessionError
from fb_crawl.core.models import (
    AuthenticatedAction,
    ScrapeMode,
    ScrapeRequest,
    ScrapeResult,
    ScrapeStats,
)
from fb_data_pipeline.config import PipelineSettings
from fb_crawl.services.execution_control import (
    NOOP_EXECUTION_CONTROL,
    NOOP_NAVIGATION_PACER,
)

from fb_crawl.composition.authenticated import (
    AuthenticatedCleanupError,
    _build_authenticated_components,
    build_authenticated_components,
    saved_session_only_credentials,
)


def _assert_cleanup_diagnostics_are_sanitized(
    error: BaseException,
    *secrets: str,
) -> None:
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        rendered = "".join(traceback.format_exception(current))
        for secret in secrets:
            assert secret not in str(current)
            assert secret not in repr(current)
            assert secret not in rendered
        assert current.__cause__ is None
        assert current.__context__ is None
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        pending.extend(
            value
            for value in vars(current).values()
            if isinstance(value, BaseException)
        )


def test_composition_import_does_not_import_runtime_dependencies() -> None:
    """Break caught: API import reaches CLI, browser, or exporter dependencies."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import fb_crawl.composition.authenticated; "
                "assert not any(name == 'argparse' or name == 'selenium' "
                "or name.startswith('selenium.') "
                "or name == 'fb_crawl.exporters' "
                "or name.startswith('fb_crawl.exporters.') "
                "for name in sys.modules)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_component_builder_does_not_import_exporters() -> None:
    """Break caught: a worker builder loads CLI output code as a side effect."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from fb_crawl.config import BrowserSettings; "
                "from fb_crawl.composition.authenticated import "
                "build_authenticated_components; "
                "components = build_authenticated_components(BrowserSettings(), lambda: ('a', 'b')); "
                "components.create_service(); "
                "assert not any(name == 'argparse' "
                "or name == 'fb_crawl.exporters' "
                "or name.startswith('fb_crawl.exporters.') "
                "for name in sys.modules)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_saved_session_only_credentials_never_prompts(monkeypatch) -> None:
    """Break caught: workers can trigger interactive Facebook credential entry."""
    import builtins
    import getpass

    monkeypatch.setattr(
        builtins,
        "input",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not prompt")),
    )
    monkeypatch.setattr(
        getpass,
        "getpass",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not prompt")),
    )

    with pytest.raises(
        SessionError,
        match="^Saved Facebook session requires manual login\\.$",
    ):
        saved_session_only_credentials()


def test_component_builder_wires_one_control_and_pacer_everywhere() -> None:
    """Break caught: a job collector silently bypasses its shared safety controls."""
    control = object()
    pacer = object()
    components = _build_authenticated_components(
        BrowserSettings(),
        lambda: ("email", "password"),
        control=control,
        navigation_pacer=pacer,
    )

    service = components.create_service()
    authenticated = service._service

    assert authenticated._control is control
    assert authenticated._session._control is control
    assert authenticated._session._navigation_pacer is pacer
    assert authenticated._session._store._control is control
    assert authenticated._session._store._navigation_pacer is pacer
    assert authenticated._members._control is control
    assert authenticated._members._navigation_pacer is pacer
    assert authenticated._comments._control is control
    assert authenticated._comments._navigation_pacer is pacer
    assert authenticated._relationships._control is control
    assert authenticated._relationships._navigation_pacer is pacer
    assert authenticated._reactions._control is control
    assert authenticated._reactions._navigation_pacer is pacer
    assert authenticated._profile_enricher._control is control
    assert authenticated._profile_enricher._navigation_pacer is pacer
    assert authenticated._uid_resolver._resolver._control is control
    assert authenticated._uid_resolver._resolver._navigation_pacer is pacer


def test_public_component_builder_uses_compatibility_controls() -> None:
    """Break caught: interactive CLI construction gains worker-only controls."""
    components = build_authenticated_components(
        BrowserSettings(),
        lambda: ("email", "password"),
    )

    service = components.create_service()._service

    assert service._control is NOOP_EXECUTION_CONTROL
    assert service._members._navigation_pacer is NOOP_NAVIGATION_PACER


def test_worker_session_delegates_and_closes_provider_then_browser(
    monkeypatch,
) -> None:
    """Break caught: worker composition bypasses the checkpoint or ingestion runtime."""
    trace: list[str] = []
    request = ScrapeRequest(
        mode=ScrapeMode.AUTHENTICATED,
        action=AuthenticatedAction.MEMBERS,
        targets=("https://www.facebook.com/groups/1",),
    )
    result = ScrapeResult(records=(), issues=(), stats=ScrapeStats(1, 0, 0, 0))
    report = object()
    control = object()
    pacer = object()

    class Browser:
        def quit(self) -> None:
            trace.append("browser.close")

    class Service:
        def validate(self, received) -> None:
            assert received is request
            trace.append("validate")

        def run(self, received, browser):
            assert received is request
            assert isinstance(browser, Browser)
            trace.append("run")
            return result

    def build_components(settings, credentials, *, control, navigation_pacer):
        assert settings == BrowserSettings()
        assert control is globals_control
        assert navigation_pacer is globals_pacer
        with pytest.raises(SessionError):
            credentials()
        return composition.AuthenticatedComponents(
            lambda settings: Browser(),
            Service,
        )

    globals_control = control
    globals_pacer = pacer
    monkeypatch.setattr(composition, "_build_authenticated_components", build_components)
    monkeypatch.setattr(
        composition,
        "build_authenticated_persistence",
        lambda settings: composition.AuthenticatedPersistenceRuntime(
            ingest_result=lambda received: report,
            close=lambda: trace.append("provider.close"),
        ),
    )

    with composition.open_authenticated_job_session(
        BrowserSettings(),
        PipelineSettings(),
        control,
        pacer,
    ) as session:
        session.validate(request)
        assert session.run(request) is result
        assert session.ingest(result) is report

    assert trace == ["validate", "run", "provider.close", "browser.close"]


@pytest.mark.parametrize("provider_fails,browser_fails", [(True, False), (False, True), (True, True)])
def test_worker_session_attempts_both_closes_without_masking_primary_stop(
    monkeypatch,
    provider_fails: bool,
    browser_fails: bool,
) -> None:
    """Break caught: cleanup masks a typed worker stop or skips a later close."""
    trace: list[str] = []

    class Browser:
        def quit(self) -> None:
            trace.append("browser.close")
            if browser_fails:
                raise RuntimeError("browser close failed")

    class Service:
        def validate(self, request) -> None:
            return None

        def run(self, request, browser):
            raise SessionError("primary stop")

    def close_provider() -> None:
        trace.append("provider.close")
        if provider_fails:
            raise RuntimeError("provider close failed")

    monkeypatch.setattr(
        composition,
        "_build_authenticated_components",
        lambda *args, **kwargs: composition.AuthenticatedComponents(
            lambda settings: Browser(),
            Service,
        ),
    )
    monkeypatch.setattr(
        composition,
        "build_authenticated_persistence",
        lambda settings: composition.AuthenticatedPersistenceRuntime(
            ingest_result=lambda result: object(),
            close=close_provider,
        ),
    )

    with pytest.raises(SessionError, match="^primary stop$"):
        with composition.open_authenticated_job_session(
            BrowserSettings(),
            PipelineSettings(),
            object(),
            object(),
        ) as session:
            session.run(
                ScrapeRequest(
                    mode=ScrapeMode.AUTHENTICATED,
                    action=AuthenticatedAction.MEMBERS,
                    targets=("https://www.facebook.com/groups/1",),
                )
            )

    assert trace == ["provider.close", "browser.close"]


@pytest.mark.parametrize("failing_close", ["provider", "browser"])
def test_worker_session_raises_the_exact_single_cleanup_failure_without_primary(
    monkeypatch,
    failing_close: str,
) -> None:
    """Break caught: normal worker cleanup silently drops a close failure."""
    provider_error = RuntimeError("provider secret")
    browser_error = RuntimeError("browser secret")
    trace: list[str] = []

    class Browser:
        def quit(self) -> None:
            trace.append("browser.close")
            if failing_close == "browser":
                raise browser_error

    monkeypatch.setattr(
        composition,
        "_build_authenticated_components",
        lambda *args, **kwargs: composition.AuthenticatedComponents(
            lambda settings: Browser(),
            lambda: object(),
        ),
    )
    monkeypatch.setattr(
        composition,
        "build_authenticated_persistence",
        lambda settings: composition.AuthenticatedPersistenceRuntime(
            ingest_result=lambda result: object(),
            close=lambda: (
                trace.append("provider.close"),
                (_ for _ in ()).throw(provider_error)
                if failing_close == "provider"
                else None,
            )[-1],
        ),
    )

    expected_resource = failing_close
    with pytest.raises(AuthenticatedCleanupError) as captured:
        with composition.open_authenticated_job_session(
            BrowserSettings(), PipelineSettings(), object(), object()
        ):
            pass

    assert captured.value.resource == expected_resource
    _assert_cleanup_diagnostics_are_sanitized(
        captured.value,
        "provider secret",
        "browser secret",
    )
    assert trace == ["provider.close", "browser.close"]


def test_worker_session_groups_cleanup_failures_without_primary(monkeypatch) -> None:
    """Break caught: two independent normal cleanup failures lose their order."""
    provider_error = RuntimeError("provider secret")
    browser_error = RuntimeError("browser secret")
    trace: list[str] = []

    class Browser:
        def quit(self) -> None:
            trace.append("browser.close")
            raise browser_error

    monkeypatch.setattr(
        composition,
        "_build_authenticated_components",
        lambda *args, **kwargs: composition.AuthenticatedComponents(
            lambda settings: Browser(),
            lambda: object(),
        ),
    )
    monkeypatch.setattr(
        composition,
        "build_authenticated_persistence",
        lambda settings: composition.AuthenticatedPersistenceRuntime(
            ingest_result=lambda result: object(),
            close=lambda: (
                trace.append("provider.close"),
                (_ for _ in ()).throw(provider_error),
            )[-1],
        ),
    )

    with pytest.raises(ExceptionGroup) as captured:
        with composition.open_authenticated_job_session(
            BrowserSettings(), PipelineSettings(), object(), object()
        ):
            pass

    assert [
        child.resource
        for child in captured.value.exceptions
    ] == ["provider", "browser"]
    _assert_cleanup_diagnostics_are_sanitized(
        captured.value,
        "provider secret",
        "browser secret",
    )
    assert trace == ["provider.close", "browser.close"]


def test_worker_session_preserves_primary_and_exposes_safe_cleanup_group(
    monkeypatch,
) -> None:
    """Break caught: cleanup errors mask a typed stop or leak their contents."""
    primary = SessionError("primary stop")
    provider_error = RuntimeError("provider-secret")
    browser_error = RuntimeError("browser-secret")

    class Browser:
        def quit(self) -> None:
            raise browser_error

    class Service:
        def run(self, request, browser):
            raise primary

    monkeypatch.setattr(
        composition,
        "_build_authenticated_components",
        lambda *args, **kwargs: composition.AuthenticatedComponents(
            lambda settings: Browser(),
            Service,
        ),
    )
    monkeypatch.setattr(
        composition,
        "build_authenticated_persistence",
        lambda settings: composition.AuthenticatedPersistenceRuntime(
            ingest_result=lambda result: object(),
            close=lambda: (_ for _ in ()).throw(provider_error),
        ),
    )

    with pytest.raises(SessionError) as captured:
        with composition.open_authenticated_job_session(
            BrowserSettings(), PipelineSettings(), object(), object()
        ) as session:
            session.run(
                ScrapeRequest(
                    mode=ScrapeMode.AUTHENTICATED,
                    action=AuthenticatedAction.MEMBERS,
                    targets=("https://www.facebook.com/groups/1",),
                )
            )

    assert captured.value is primary
    cleanup_group = captured.value.cleanup_failures
    assert [child.resource for child in cleanup_group.exceptions] == [
        "provider",
        "browser",
    ]
    _assert_cleanup_diagnostics_are_sanitized(
        cleanup_group,
        "provider-secret",
        "browser-secret",
    )
    _assert_cleanup_diagnostics_are_sanitized(
        captured.value,
        "provider-secret",
        "browser-secret",
    )


@pytest.mark.parametrize("stage", ["service", "browser", "persistence"])
def test_worker_session_closes_only_constructed_resources_on_setup_failure(
    monkeypatch,
    stage: str,
) -> None:
    """Break caught: partial construction leaks a browser or changes its failure."""
    primary = RuntimeError(f"{stage} failed")
    trace: list[str] = []

    class Browser:
        def quit(self) -> None:
            trace.append("browser.close")

    def create_service():
        if stage == "service":
            raise primary
        return object()

    def create_browser(settings):
        if stage == "browser":
            raise primary
        return Browser()

    monkeypatch.setattr(
        composition,
        "_build_authenticated_components",
        lambda *args, **kwargs: composition.AuthenticatedComponents(
            create_browser=create_browser,
            create_service=create_service,
        ),
    )

    def build_persistence(settings):
        if stage == "persistence":
            raise primary
        return composition.AuthenticatedPersistenceRuntime(
            ingest_result=lambda result: object(),
            close=lambda: trace.append("provider.close"),
        )

    monkeypatch.setattr(composition, "build_authenticated_persistence", build_persistence)

    with pytest.raises(RuntimeError) as captured:
        with composition.open_authenticated_job_session(
            BrowserSettings(), PipelineSettings(), object(), object()
        ):
            pass

    assert captured.value is primary
    assert trace == ([] if stage in {"service", "browser"} else ["browser.close"])
