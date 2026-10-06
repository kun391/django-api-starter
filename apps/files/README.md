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
