from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path

from fb_crawl.accounts.postgres import PostgresAccountRepository
from fb_crawl.accounts.repository import AccountRepository
from fb_crawl.auth.config import AuthSettings
from fb_crawl.auth.email import SmtpEmailDelivery
from fb_crawl.auth.passwords import PasswordHasher
from fb_crawl.auth.rate_limit import RateLimitService
from fb_crawl.auth.service import AccountAuthService
from fb_crawl.auth.tokens import TokenService
from fb_crawl.core.exceptions import ConfigurationError
from fb_crawl.entitlements.quota import ContactQuotaService, PostgresContactQuotaRepository
from fb_crawl.entitlements.service import EntitlementService
from fb_crawl.contacts.postgres import PostgresContactRepository
from fb_crawl.contacts.service import ContactLookupService
from fb_crawl.product_jobs.postgres import PostgresProductCrawlRepository
from fb_crawl.providers.health import (
    ObservedPhoneProvider,
    PostgresProviderHealthRepository,
)
from fb_crawl.history.postgres import PostgresHistoryRepository
from fb_crawl.history.repository import HistoryRepository
from fb_crawl.history.service import HistoryService
from fb_crawl.interaction_sessions.postgres import PostgresInteractionSessionRepository
from fb_crawl.interaction_sessions.service import InteractionSessionService
from fb_crawl.interaction_sessions.lookup import SessionLookupService
from fb_crawl.exports.artifacts import ExportArtifactStore
from fb_crawl.exports.postgres import PostgresExportRepository
from fb_crawl.exports.service import ExportService
from fb_crawl.exports.metrics import (
    PostgresProductMetricsRepository,
    ProductMetricsRepository,
)
from fb_crawl.licenses.config import load_license_keyring
from fb_crawl.licenses.keys import LicenseKeyService
from fb_crawl.licenses.postgres import PostgresLicenseRepository
from fb_crawl.licenses.service import LicenseService
from fb_data_pipeline.config import PipelineSettings, load_pipeline_settings
from fb_data_pipeline.providers.fbnumber import FBNumberProvider
from fb_data_pipeline.services.pipeline import EnrichmentPipeline


@dataclass(frozen=True, slots=True)
class ProductServices:
    auth_service: AccountAuthService
    account_repository: AccountRepository
    token_service: TokenService
    rate_limiter: RateLimitService | None = None
    license_service: LicenseService | None = None
    entitlement_service: EntitlementService | None = None
    quota_service: ContactQuotaService | None = None
    contact_lookup_service: ContactLookupService | None = None
    history_repository: HistoryRepository | None = None
    export_service: ExportService | None = None
    metrics_repository: ProductMetricsRepository | None = None
    product_crawl_repository: object | None = None
    facebook_session_available: bool = False
    provider_health_repository: object | None = None
    interaction_session_service: InteractionSessionService | None = None
    interaction_session_lookup_service: SessionLookupService | None = None


def compose_product_services(
    database_url: str,
    auth_settings: AuthSettings,
    env: Mapping[str, str],
    *,
    statement_timeout_seconds: float = 5.0,
    pipeline_settings: PipelineSettings | None = None,
) -> ProductServices:
    """Compose product-only services without importing browser code."""

    repository = PostgresAccountRepository(
        database_url,
        statement_timeout_seconds=statement_timeout_seconds,
    )
    token_service = TokenService(
        jwt_secret=auth_settings.jwt_secret,
        token_hmac_secret=auth_settings.token_hmac_secret,
        access_ttl_seconds=auth_settings.access_ttl_seconds,
    )
    try:
        smtp_port = int(env.get("LEAD_FINDER_SMTP_PORT", "587"))
    except ValueError as error:
        raise ConfigurationError(
            "LEAD_FINDER_SMTP_PORT must be an integer."
        ) from error
    email_delivery = SmtpEmailDelivery(
        host=env.get("LEAD_FINDER_SMTP_HOST", "localhost").strip(),
        port=smtp_port,
        username=env.get("LEAD_FINDER_SMTP_USERNAME", ""),
        password=env.get("LEAD_FINDER_SMTP_PASSWORD", ""),
        sender=env.get(
            "LEAD_FINDER_SMTP_SENDER",
            "Lead Finder <noreply@localhost>",
        ),
    )
    password_hasher = PasswordHasher()
    rate_limiter = RateLimitService(
        repository,
        auth_settings.token_hmac_secret,
    )
    auth_service = AccountAuthService(
        repository=repository,
        password_hasher=password_hasher,
        token_service=token_service,
        email_delivery=email_delivery,
        rate_limiter=rate_limiter,
        public_base_url=auth_settings.public_base_url,
    )
    license_repository = PostgresLicenseRepository(
        database_url,
        statement_timeout_seconds=statement_timeout_seconds,
    )
    license_service = LicenseService(
        license_repository,
        LicenseKeyService(load_license_keyring(env)),
    )
    entitlement_service = EntitlementService(license_repository)
    quota_service = ContactQuotaService(
        PostgresContactQuotaRepository(
            database_url,
            statement_timeout_seconds=statement_timeout_seconds,
        ),
        auth_settings.product_timezone,
    )
    resolved_pipeline_settings = pipeline_settings or load_pipeline_settings(env)
    provider_health_repository = PostgresProviderHealthRepository(
        database_url,
        statement_timeout_seconds=statement_timeout_seconds,
    )
    contact_lookup_service = None
    if resolved_pipeline_settings.fb_number_api_token:
        provider = ObservedPhoneProvider(
            FBNumberProvider.from_settings(resolved_pipeline_settings),
            provider_health_repository,
            configured=True,
        )
        contact_lookup_service = ContactLookupService(
            entitlement_service,
            quota_service,
            PostgresContactRepository(
                database_url,
                statement_timeout_seconds=statement_timeout_seconds,
            ),
            EnrichmentPipeline(provider),
        )
    export_repository = PostgresExportRepository(
        database_url,
        statement_timeout_seconds=statement_timeout_seconds,
    )
    export_artifacts = ExportArtifactStore(
        env.get("LEAD_FINDER_EXPORT_DIR", "runtime/lead-finder-exports")
    )
    return ProductServices(
        auth_service=auth_service,
        account_repository=repository,
        token_service=token_service,
        rate_limiter=rate_limiter,
        license_service=license_service,
        entitlement_service=entitlement_service,
        quota_service=quota_service,
        contact_lookup_service=contact_lookup_service,
        history_repository=PostgresHistoryRepository(
            database_url,
            statement_timeout_seconds=statement_timeout_seconds,
        ),
        export_service=ExportService(export_repository, export_artifacts),
        metrics_repository=PostgresProductMetricsRepository(
            database_url,
            statement_timeout_seconds=statement_timeout_seconds,
        ),
        product_crawl_repository=PostgresProductCrawlRepository(
            database_url,
            statement_timeout_seconds=statement_timeout_seconds,
        ),
        facebook_session_available=_session_is_available(env),
        provider_health_repository=provider_health_repository,
        interaction_session_service=InteractionSessionService(
            PostgresInteractionSessionRepository(database_url, statement_timeout_seconds=statement_timeout_seconds),
            HistoryService(PostgresHistoryRepository(database_url, statement_timeout_seconds=statement_timeout_seconds), quota_service),
        ),
        interaction_session_lookup_service=SessionLookupService(
            PostgresInteractionSessionRepository(database_url, statement_timeout_seconds=statement_timeout_seconds),
            contact_lookup_service,
        ),
    )


def _session_is_available(env: Mapping[str, str]) -> bool:
    raw = env.get("FB_CRAWL_SESSION_PATH", "runtime/session.json").strip()
    if not raw:
        return False
    try:
        return Path(raw).expanduser().resolve().is_file()
    except OSError:
        return False
