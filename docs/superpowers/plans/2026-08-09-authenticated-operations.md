# Authenticated Operations Implementation Plan

1. Extend models and the unified schema with enrichment-v2, provenance,
   timestamps, and engagement fields while preserving defaults.
2. Add directory-work URL routing, content readiness, parser fixtures, and
   conservative visible-field parsing.
3. Add a reaction-aware parser and combined engagement orchestration.
4. Extend batch classification with typed lines and mixed result/output
   handling.
5. Add an atomic JSON checkpoint adapter and service/CLI resume and incremental
   behavior.
6. Add sanitized inspect diagnostics, model, collector, exporter, and CLI.
7. Document commands and future external-provider/PostgreSQL boundaries.
8. Run focused tests after each slice, then the complete test, compile,
   dependency, CLI-help, repository-safety, and diff checks.
