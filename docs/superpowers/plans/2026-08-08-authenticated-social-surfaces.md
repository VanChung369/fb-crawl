# Authenticated Social Surfaces Implementation Plan

1. Extend authenticated actions, message records, and URL normalization with
   unit tests.
2. Add bounded browser collectors for profile relationships, reactions, and an
   explicit message thread.
3. Add conservative visible-user and message parsers using synthetic fixtures.
4. Route new actions through `AuthenticatedService`, preserving session-fatal
   and target-isolated error behavior.
5. Add the separate message exporter and authenticated writer dispatch.
6. Add CLI commands, defaults, validation, help text, and documentation.
7. Run unit/integration tests, compile checks, dependency checks, CLI help smoke
   tests, and repository safety checks.
