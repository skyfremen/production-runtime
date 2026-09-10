# Production Runtime

Stateless execution code for an externally owned private state repository.

The only normal entry point is `.github/workflows/run.yml`, triggered manually
with an opaque `batch_id` and exact `source_sha`. Canonical requests, recovery
evidence, receipts, media registry state, and analytics are never committed here.
The production job runs once per batch with bounded internal concurrency of two.

