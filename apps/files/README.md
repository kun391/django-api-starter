# Private files

Owner-only private uploads, metadata, downloads, immutable replacement and logical
deletion. No public listing, sharing, tenant model, business-object attachment
manager or automatic staff override is included.

`services.py` owns mutations and compensation. `validation.py` owns content
policies and receive-side bounds. Django's Storage API is the external boundary;
`backends.py` only makes filesystem URLs unavailable. `handlers.py` reuses the
core outbox for retryable physical deletion.

Read [the storage guide](../../docs/storage.md) before enabling S3 or adding a
purpose/validator. Authorization for attachment to an order/document belongs to
that business module, not to client-provided object keys or owner IDs.


## Phase 21 tombstone retention

Successful storage deletion now records the first verified `deleted_at`
timestamp while preserving the existing periodic recheck behavior. The retention
module may later remove a scrubbed DELETED manifest only after the configured
window and only when no ticket attachment references it. Retention never performs
storage I/O and never deletes READY/PENDING/DELETING manifests.
