# Authenticated Social Surfaces Design

## Scope

Extend authenticated CLI mode with explicit collection for direct profiles,
visible friends, visible followers, visible post reactions, and visible text
from an explicitly selected conversation.

The implementation must not infer hidden profile fields, enumerate the inbox,
bypass privacy controls, or alter Facebook state such as adding a reaction.

## Commands

```text
fb-crawl authenticated profile PROFILE_URL [PROFILE_URL ...]
fb-crawl authenticated friends PROFILE_URL [PROFILE_URL ...]
fb-crawl authenticated followers PROFILE_URL [PROFILE_URL ...]
fb-crawl authenticated reactions POST_URL [POST_URL ...]
fb-crawl authenticated messages THREAD_URL [THREAD_URL ...]
```

`profile` implies enrichment. Friends, followers, and reactions may opt into
the existing bounded enrichment pipeline. Messages reject enrichment flags and
use a separate record/output schema.

## Boundaries

- Profile targets accept normalized vanity and `profile.php?id=` forms.
- Friends/followers normalize to exactly one requested relation route.
- Reactions accept the existing supported comment/post target forms and only
  click the visible reactions summary/dialog control.
- Messages accept only `/messages/t/THREAD_ID`; `/messages` is rejected.
- Every scroll loop is bounded by `--steps` and `--delay`.
- Session loss is fatal. Navigation and parsing failures remain target-scoped.
- No raw HTML, cookie, password, screenshot, or message cache is persisted.

## Output

Profile, friends, followers, and reactions reuse the unified user schema.
Messages use:

```text
message_id,sender_name,sender_profile_url,text,sent_at,thread_url,source,error_code,error_message
```

A generated `visible-*` message ID is explicitly a deterministic local capture
identifier when the visible DOM has no stable Facebook message ID.

## Privacy semantics

An empty visible list or absent field is not guessed from other sources.
Privacy-restricted content remains unavailable. Documentation must state that
the authenticated account's manual visibility is the upper bound.
