from __future__ import annotations

import time
from collections import deque
from dataclasses import replace
from typing import Protocol


from fb_crawl.adapters.http.client import HttpClient
from fb_crawl.core.exceptions import (
    FetchError,
    ParseError,
    ValidationError,
)
from fb_crawl.core.models import (
    PageRecord,
    PublicAction,
    ScrapeIssue,
    ScrapeMode,
    ScrapeRequest,
    ScrapeResult,
    ScrapeStats,
    TargetKind,
)
from fb_crawl.core.urls import (
    canonicalize_targets,
    normalize_facebook_url,
    normalize_group_url,
)


class DiscoveryPort(Protocol):
    def search(
        self,
        keyword: str,
        target: TargetKind,
        limit: int,
    ) -> list[str]: ...

    def from_html(
        self,
        html: str,
        *,
        base_url: str,
        target: TargetKind,
        limit: int,
    ) -> list[str]: ...


class PageParserPort(Protocol):
    def parse(
        self,
        html: str,
        canonical_url: str,
    ) -> PageRecord: ...


class ContactEnricherPort(Protocol):
    def enrich(
        self,
        record: PageRecord,
        facebook_html: str,
    ) -> tuple[
        PageRecord,
        tuple[ScrapeIssue, ...],
    ]: ...


class PublicService:
    def __init__(
        self,
        client: HttpClient,
        discovery: DiscoveryPort,
        parser: PageParserPort,
        enricher: ContactEnricherPort,
        *,
        sleep_func=time.sleep,
    ) -> None:
        self._client = client
        self._discovery = discovery
        self._parser = parser
        self._enricher = enricher
        self._sleep = sleep_func

    def _initial_targets(
        self,
        request: ScrapeRequest,
    ) -> list[tuple[str, str]]:
        if request.mode is not ScrapeMode.PUBLIC:
            raise ValidationError("PublicService requires public mode.")

        action = PublicAction(request.action)

        if action is PublicAction.SEARCH:
            if not request.keyword or not request.keyword.strip():
                raise ValidationError("Search requires a non-empty keyword.")

            keyword = request.keyword.strip()

            return [
                (
                    url,
                    f"keyword:{keyword}",
                )
                for url in self._discovery.search(
                    keyword,
                    request.target_kind,
                    request.limit,
                )
            ]

        seeds: list[tuple[str, str]] = []
        seen: set[str] = set()

        def add(
            url: str,
            source: str,
        ) -> None:
            if url not in seen and len(seeds) < request.limit:
                seen.add(url)
                seeds.append((url, source))

        direct_targets = canonicalize_targets(
            request.targets,
            target=request.target_kind,
            limit=request.limit,
        )

        for target in direct_targets:
            add(target, "seed")

        if action is PublicAction.CRAWL:
            for raw_target in request.targets:
                group_url = normalize_group_url(raw_target)

                if not group_url or len(seeds) >= request.limit:
                    continue

                group_html = self._client.get_text(group_url)

                discovered = self._discovery.from_html(
                    group_html,
                    base_url=group_url,
                    target=request.target_kind,
                    limit=(request.limit - len(seeds)),
                )

                for target in discovered:
                    add(target, group_url)

        if not seeds:
            raise ValidationError(
                ("No valid public Facebook " "targets were provided.")
            )

        return seeds

    def run(
        self,
        request: ScrapeRequest,
    ) -> ScrapeResult[PageRecord]:
        action = PublicAction(request.action)
        seeds = self._initial_targets(request)

        targets = [url for url, _ in seeds]

        initial_discovered = sum(source != "seed" for _, source in seeds)

        queue = deque(
            (
                url,
                0,
                source,
            )
            for url, source in seeds[: request.max_nodes]
        )

        queued = set(targets[: request.max_nodes])
        visited: set[str] = set()
        records: list[PageRecord] = []
        issues: list[ScrapeIssue] = []
        failed = 0

        while queue and len(records) + failed < request.max_nodes:
            url, depth, source = queue.popleft()
            queued.discard(url)

            if url in visited:
                continue

            visited.add(url)

            try:
                html = self._client.get_text(url)

                parsed = self._parser.parse(
                    html,
                    url,
                )

                (
                    enriched,
                    enrichment_issues,
                ) = self._enricher.enrich(
                    parsed,
                    html,
                )

                records.append(
                    replace(
                        enriched,
                        depth=depth,
                        discovery_source=source,
                    )
                )

                issues.extend(enrichment_issues)

            except (
                FetchError,
                ParseError,
            ) as error:
                failed += 1

                issues.append(
                    ScrapeIssue(
                        code=error.code,
                        message=error.safe_message,
                        target=(error.target or url),
                        mode=ScrapeMode.PUBLIC,
                        action=action.value,
                        retryable=isinstance(
                            error,
                            FetchError,
                        ),
                    )
                )

                continue

            if action is PublicAction.CRAWL and depth < request.depth:
                discovered = self._discovery.from_html(
                    html,
                    base_url=url,
                    target=request.target_kind,
                    limit=request.max_nodes,
                )

                for candidate in discovered:
                    normalized = normalize_facebook_url(candidate)

                    if not normalized or normalized in visited or normalized in queued:
                        continue

                    if len(records) + failed + len(queue) >= request.max_nodes:
                        break

                    queue.append(
                        (
                            normalized,
                            depth + 1,
                            url,
                        )
                    )
                    queued.add(normalized)

            if request.delay_seconds > 0 and queue:
                self._sleep(request.delay_seconds)

        return ScrapeResult(
            records=tuple(records),
            issues=tuple(issues),
            stats=ScrapeStats(
                requested=len(targets),
                discovered=(
                    initial_discovered
                    + max(
                        0,
                        len(visited) - len(targets),
                    )
                ),
                succeeded=len(records),
                failed=failed,
            ),
        )
