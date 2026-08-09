from __future__ import annotations

from typing import Protocol

from fb_data_pipeline.core.models import FacebookIdentity, ProviderResult


class PhoneEnrichmentProvider(Protocol):
    name: str

    def search(self, identity: FacebookIdentity) -> ProviderResult: ...

    def close(self) -> None: ...

