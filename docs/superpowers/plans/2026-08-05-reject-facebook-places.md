# Reject Facebook Places URLs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject every Facebook `/places/...` URL before it can be treated as a public page or profile target.

**Architecture:** Keep the behavior at the dependency-free core URL boundary by classifying `places` as an internal Facebook path. Existing callers of `normalize_facebook_url` and `canonicalize_targets` then inherit the rejection without CLI, service, discovery, parser, or exporter changes.

**Tech Stack:** Python 3.12+, pytest, standard-library URL parsing.

## Global Constraints

- Use TDD: observe the focused regression test fail before modifying production code.
- Do not add Places discovery behavior.
- Do not change followers, authenticated mode, API, WebUI, or group handling.
- Do not call Facebook or any live network endpoint from automated tests.
- Keep `D:/project/fb/craw` and `D:/project/fb/Facebook-Data-Scraping-Tools` unchanged.
- Commit only the core URL rule and its regression test in the implementation commit.

---

## Locked File Map

- `tests/unit/core/test_urls.py`: regression coverage for direct normalization and canonical target filtering.
- `src/fb_crawl/core/urls.py`: central Facebook internal-path classification.

---

### Task 1: Reject Facebook Places targets

**Files:**
- Modify: `tests/unit/core/test_urls.py`
- Modify: `src/fb_crawl/core/urls.py`

**Interfaces:**
- Consumes: `normalize_facebook_url(value, *, base_url=None) -> str | None`; `canonicalize_targets(values, *, target, limit) -> list[str]`; `TargetKind.PAGES`.
- Produces: Places URLs return `None` from normalization and never appear in canonical public targets.

- [ ] **Step 1: Add the failing Places regression test**

Append this test to `tests/unit/core/test_urls.py`:

```python
def test_rejects_places_urls_from_page_targets() -> None:
    places_url = (
        "https://www.facebook.com/places/"
        "Hoat-dong-giai-tri-tai-Ha-Noi/106388046062960/"
    )

    assert normalize_facebook_url(places_url) is None
    assert normalize_facebook_url(
        "https://m.facebook.com/places/restaurants/12345/"
    ) is None

    assert canonicalize_targets(
        [
            places_url,
            "https://www.facebook.com/example",
        ],
        target=TargetKind.PAGES,
        limit=5,
    ) == ["https://www.facebook.com/example"]
```

- [ ] **Step 2: Run the focused test and verify the current bug**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\unit\core\test_urls.py::test_rejects_places_urls_from_page_targets `
  -v
```

Expected: the test fails because the first Places URL currently normalizes to `https://www.facebook.com/places` instead of `None`.

- [ ] **Step 3: Add Places to the central internal-path set**

In `src/fb_crawl/core/urls.py`, find these adjacent entries in `FACEBOOK_INTERNAL_PATHS`:

```python
    "photos",
    "plugins",
```

Replace them with:

```python
    "photos",
    "places",
    "plugins",
```

Do not add a separate conditional or modify service/discovery code.

- [ ] **Step 4: Run the focused URL module**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\core\test_urls.py -v
```

Expected: all four URL tests pass.

- [ ] **Step 5: Run the complete offline test suite and syntax check**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q src tests
git diff --check
```

Expected: 29 tests pass, compileall exits `0`, and `git diff --check` reports no errors.

- [ ] **Step 6: Review and commit the implementation**

Run:

```powershell
git status --short
git diff -- src/fb_crawl/core/urls.py tests/unit/core/test_urls.py
git add src/fb_crawl/core/urls.py tests/unit/core/test_urls.py
git commit -m "fix: reject Facebook Places URLs"
```

Expected before commit: only `src/fb_crawl/core/urls.py` and `tests/unit/core/test_urls.py` are modified. Expected after commit: the working tree is clean.

---

## Completion Boundary

The task is complete when Places URLs are rejected by the central normalizer, all offline tests pass, and the focused implementation commit is clean. Followers and Places discovery remain explicitly out of scope.
