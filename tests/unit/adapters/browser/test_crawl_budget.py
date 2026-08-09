from fb_crawl.adapters.browser.crawl_budget import CrawlBudget


def test_budget_without_limits_allows_until_collector_exhaustion() -> None:
    budget = CrawlBudget(steps=None, max_duration_seconds=None)

    assert budget.allows(0) is True
    assert budget.allows(1_000_000) is True


def test_budget_stops_at_steps_or_duration_whichever_arrives_first() -> None:
    values = iter([10.0, 10.5, 11.1])
    budget = CrawlBudget(
        steps=5,
        max_duration_seconds=1.0,
        monotonic_func=lambda: next(values),
    )

    assert budget.allows(0) is True
    assert budget.allows(1) is False

    step_budget = CrawlBudget(steps=2, max_duration_seconds=None)
    assert step_budget.allows(1) is True
    assert step_budget.allows(2) is False
