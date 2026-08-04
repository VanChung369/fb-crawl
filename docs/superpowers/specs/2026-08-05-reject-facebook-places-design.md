# Reject Facebook Places URLs Design

**Date:** 2026-08-05
**Status:** Approved

## Goal

Prevent Facebook Places directory URLs from being accepted as public page or profile targets.

## Problem

The public URL normalizer currently treats the first path segment as a page username. Because `places` is not classified as an internal Facebook path, an input such as:

```text
https://www.facebook.com/places/Hoat-dong-giai-tri-tai-Ha-Noi/106388046062960/
```

is incorrectly reduced to:

```text
https://www.facebook.com/places
```

That generic directory URL is then processed as if it were a page record, producing misleading or empty output.

## Design

Add `places` to the central `FACEBOOK_INTERNAL_PATHS` set in `fb_crawl.core.urls`.

This makes the existing URL boundary reject all `/places/...` variants before they reach discovery, parsing, enrichment, or service orchestration. The behavior remains centralized and automatically applies to direct page inputs, search results, and crawl-discovered candidates.

Expected behavior:

| Input | Result |
|---|---|
| `https://www.facebook.com/places/...` | Rejected (`None`) |
| `https://m.facebook.com/places/...` | Rejected (`None`) |
| `https://www.facebook.com/example` | Preserved as a page target |
| `https://www.facebook.com/profile.php?id=12345` | Preserved as a people target |
| `https://www.facebook.com/groups/example` | Still handled only by the existing group-seed path |

## Testing

Extend the core URL regression tests to prove that:

- a full Facebook Places URL normalizes to `None`;
- canonical target collection filters Places while retaining a valid page URL;
- existing page, profile, asset, internal-path, and group behavior remains green.

Run the focused URL tests followed by the complete offline test suite.

## Scope Boundary

This change does not:

- treat Places as a discovery seed;
- add follower counts or follower-profile collection;
- add authenticated browsing, GraphQL pagination, API, or WebUI behavior;
- change the existing handling of groups.
