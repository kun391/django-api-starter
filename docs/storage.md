# Private file storage and secure uploads (Phase 11)

## Scope and compatibility

`apps.files` provides owner-only uploads, metadata, downloads, immutable replacement
and logical deletion. It uses Django Storage, not a new storage framework. The
minimal web + PostgreSQL stack needs no S3, Redis or worker. The existing `storage`
extra enables S3-compatible deployments; no dependency versions change in this phase.

**Compatibility change:** Django's default file storage is now private, alongside
`storages['private']`. The DEBUG-only public `/media/` route has been removed.
WhiteNoise/static assets remain separate. Existing application forks that relied
on public media URLs must deliberately migrate that behavior; old files are not
moved automatically. Do not place private files under a public media/static root.

This is a bounded foundation, not a general file manager. No file listing, sharing,
public links, tenant framework, automatic staff override, business-attachment
manager, antivirus, PDF/image conversion, resumable upload or direct-to-S3 upload
is included. File validation is NOT proof that a document is malware-free.

## HTTP API

All routes require the existing TokenAuthentication or SessionAuthentication;
sessions retain CSRF enforcement. Only the owner can act on a file, including
staff users. Inaccessible/non-ready IDs are 404 rather than revealing ownership.
IDs are UUIDs. Clients never submit storage keys, paths, URLs or owner IDs.

| Method / route | Behavior |
| --- | --- |
| `POST /api/v1/files/` | One multipart `file`, optional `purpose=document`; 201 metadata |
| `GET /api/v1/files/{id}/` | Ready file metadata |
| `GET /api/v1/files/{id}/download/` | Authorized attachment download |
| `POST /api/v1/files/{id}/replace/` | One multipart `file`; 201 with a NEW ID, preserving purpose |
| `DELETE /api/v1/files/{id}/` | 204; revoke API access and durably request physical deletion |
| `POST /api/v1/files/{id}/download-url/` | Optional signed S3 GET URL; disabled by default |

Unknown/repeated multipart fields and any query parameters are rejected. No JSON
base64 upload is supported. Metadata includes `id`, `purpose`, `original_name`,
`size`, `media_type`, `sha256` and `created_at`, never the storage key. No Phase 7
Idempotency-Key replay is enabled: retrying a successful upload after losing its
response may create a second ready file. Product-level command idempotency and
attachment identity are separate design decisions, not a storage guarantee.

Example using an existing token from the normal authentication API (do not put
real credentials into source control, logs or shared shell history):

```bash
# API_TOKEN must be provided securely in the environment.
curl --fail-with-body http://localhost:5001/api/v1/files/ \
  -H "Authorization: Token $API_TOKEN" \
  -F purpose=document -F 'file=@notes.txt;type=text/plain'

# Use the returned UUID in FILE_ID.
curl --fail-with-body "http://localhost:5001/api/v1/files/$FILE_ID/download/" \
  -H "Authorization: Token $API_TOKEN" \
  -H 'Accept: application/octet-stream' --output downloaded.txt
```

The API preserves Phase 7 Problem Details and request IDs. Validation failures
are 400, wrong request media type 415, file size/count limits 413, upload conflict
409, upload throttle 429 with Retry-After, and translated storage I/O failures
503 `file_storage_unavailable`. DB/programming/configuration failures are not
misrepresented as storage failures. Malformed multipart/form-data and Django's
own form-field limits can produce 400 before file validation.

All file API responses are no-store/nosniff/no-referrer. Downloads use
`application/octet-stream` and attachment disposition, not inline content. CORS
exposes Content-Disposition. No ETag caching from Phase 10 is enabled for files.
The download route stages a bounded snapshot and verifies size/SHA-256 before
returning FileResponse; late backend-read errors become 503 before response
headers are sent. The response then streams the staged spool, which spills above
1 MiB. This is not zero-copy remote streaming. Disk/client failures after headers
are sent cannot be transformed into a new JSON response.

## Upload policy and resource bounds

The server-owned `PRIVATE_FILE_POLICIES` maps each purpose to a maximum size and
extension-to-validator functions. The initial `document` policy accepts only:

- `.txt`: UTF-8, without ASCII control bytes other than tab/CR/LF.
- `.json`: UTF-8 parsed JSON, rejecting non-finite constants and float overflow.

HTML embedded in a `.txt` is still plain text, not sanitized HTML; never render
uploaded text as trusted markup. JSON number limits follow Python's parser and
finite float representation, not arbitrary precision interchange. PDFs, images,
archives, SVG and executables are not silently accepted under generic MIME rules.

Filenames are NFKC-normalized, length-limited and allowlisted; path separators,
control/bidi characters, leading punctuation, `..` and trailing dot/space are
rejected by the service. Django may first strip a submitted path to its basename;
that name is only display metadata. The storage path is always derived from a
fresh server UUID, e.g. `objects/<prefix>/<uuid>`, never a submitted name. Declared
MIME and UploadedFile.size are not trusted as evidence of content or byte length.

`PRIVATE_FILE_MAX_BYTES` defaults to 5 MiB and is capped at 16 MiB. Validation reads
at most policy-limit + 1 bytes; empty files are rejected. A receive-side Django
upload handler stops file spooling when the byte limit or one-file limit is hit.
It applies to `/api/v1/files/`, including session-auth parsing, without changing
unrelated endpoint parsers. The multipart parser rejects declared request bodies
larger than the file limit + 64 KiB before parsing. Set the trusted reverse proxy's
request-body, request-time and connection limits as well: this handler does not
control bytes already buffered by a proxy, web server or ASGI server.

Uploads and replacements share a PostgreSQL-backed actor limit, default 20 per
60 seconds, independent of Redis and spoofed client IP headers. This is not a
lifetime storage quota or a volumetric DDoS defense. A product needs explicit
per-user/tenant storage quotas, edge limits and capacity monitoring.

To add a purpose, configure trusted Python code, not client-supplied import paths:

```python
PRIVATE_FILE_POLICIES['machine-export'] = {
    'max_bytes': 512 * 1024,
    'validators': {'.json': 'apps.files.validation.validate_json'},
}
```

The validator receives bounded bytes, raises Django ValidationError for rejected
content, and returns a media type. Review purpose-specific permissions as well
as format checks. The foundation lets authenticated users select configured
purposes; it does not invent permission semantics for future business purposes.
Use a quarantine/scanning/release design before supporting higher-risk formats.

## Filesystem deployment

Default root is `<BASE_DIR>/private-files`; override with an absolute
`PRIVATE_FILE_ROOT` when deploying. Configuration rejects overlap in either
direction with media/static roots, including resolved symlinks. Do not expose
this directory through Nginx aliases, static hosting, public volumes or a CDN.
The application account must control the directory; untrusted local users must
not be able to insert symlinks or replace files there. Files use mode 0600 and
created directories 0700. `PrivateFileSystemStorage.url()` deliberately raises.

Compose mounts `private_files_volume` at `/app/private-files` for both web and
the optional worker. They must run with compatible filesystem ownership. All
replicas and cleanup processes must see the SAME persistent storage root. A
container-local directory on each replica is not shared storage. Do not use
`docker compose down -v` when data must be retained.

Local verification after migrations:

```bash
uv run python manage.py migrate
uv run pytest tests/test_private_files.py tests/test_private_file_edges.py \
  tests/test_private_storage_config.py
```

These tests use temporary private roots and real filesystem I/O. They do not
write into a production bucket.

## Optional S3-compatible deployment

Install the existing extra, and retain it on uv commands:

```bash
uv sync --locked --extra storage --group dev
uv run --extra storage python manage.py check
uv run --extra storage python manage.py migrate
```

```text
PRIVATE_FILE_BACKEND=s3
PRIVATE_FILE_S3_BUCKET=<pre-provisioned-private-bucket>
PRIVATE_FILE_S3_REGION=us-east-1
PRIVATE_FILE_S3_ENDPOINT=https://<compatible-endpoint>
PRIVATE_FILE_SIGNED_DOWNLOADS=False
PRIVATE_FILE_URL_TTL=60
```

Leave endpoint unset for AWS's regional endpoint. Credentials use boto3's normal
provider chain, such as a workload role or securely injected AWS_ACCESS_KEY_ID,
AWS_SECRET_ACCESS_KEY and optional AWS_SESSION_TOKEN; no credentials in URLs.
The bucket is provisioned outside this application. Use HTTPS, least-privilege
object access and public-access blocking; private object defaults do not override
a public bucket policy. `PRIVATE_FILE_S3_ALLOW_HTTP=True` is only for explicitly
trusted local test endpoints, never an internet-facing production deployment.

The backend has no custom public domain, enables signed query authentication,
uses SigV4/path addressing, exact immutable names, bounded client retries and
connect/read timeouts. Stored objects have octet-stream, attachment and no-store
metadata. Client timeouts are not a hard total upload deadline; interrupted
network writes can still complete remotely.

Build the storage-enabled production image:

```bash
docker build --target runtime-storage -t django-api-starter-storage .
```

It starts Gunicorn by default. Run the same image with
`python manage.py dispatch_outbox --batch-size 100` or
`python manage.py reconcile_private_files --batch-size 100` under a platform
scheduler. No Celery/Redis is needed for this route. Existing `runtime-async` and
`development-async` images do NOT include the storage extra. An S3-backed Celery
worker requires an application image with BOTH extras, or an environment started
with `uv run --extra async --extra storage celery -A apps.core.celery:app worker`.
Do not start a filesystem-configured cleanup worker against S3-configured uploads.

Changing the backend/bucket/root does not migrate existing objects. Preserve the
mapping between manifests and storage until an explicit migration is complete.
With S3 versioning, delete creates a marker rather than erasing retained versions;
configure provider lifecycle/retention, multipart-abort cleanup and backups for
the actual data-erasure requirements. No provider-wide compliance claim is made.

### Signed URLs

Only enable signed downloads after reviewing this trust boundary. The API checks
current ownership and READY state, then grants a GET URL for 1-300 seconds (default
60). The response is no-store and returns `url` and `expires_in`. Forced attachment,
octet-stream and cache policy are included in the signature. The issuing process
must use an endpoint that the recipient can actually reach.

The URL is a bearer grant: another holder can use it without re-authenticating.
Logout, role changes and logical deletion cannot revoke a previously issued URL
immediately. Physical object deletion or expiry can stop later requests, but an
already authorized/downloaded copy cannot be recalled. Direct S3 access does not
perform the API's checksum staging, and response policy remains provider-owned.
Use the API download route when per-request authorization is required. Never log
query strings, signed URLs, authorization headers, file contents or full provider
exceptions in access logs/tracing/error reporting.

## Lifecycle, failure handling and cleanup

```text
validated bytes
    -> committed PENDING manifest with immutable key and expiry
    -> storage write outside the database transaction
    -> short finalization transaction
         -> READY
         -> for replacement: retire old ID + outbox event in the SAME commit

READY -> DELETING + outbox event -> physical delete -> DELETED tombstone
```

Upload/replace deliberately require default-database autocommit at the service
entry; the HTTP views opt out of ATOMIC_REQUESTS. Do not nest these operations in
another atomic block or `idempotent_post`. A failed first manifest insert never
writes a file. Failed storage/finalization schedules compensation; if compensation
cannot commit, the precommitted PENDING manifest remains for reconciliation.
The previous READY ID stays usable until replacement finalization commits.
Concurrent replacements have one winner; failed new objects enter cleanup.

DELETE is repeatable for the same owner: 204 even after logical deletion. A rollback
preserves READY state and discards the event. Authorization fails immediately
for subsequent API reads once logical deletion commits; in-flight downloads and
previously issued signed URLs have the limitations above. The physical deletion
handler acts only on DELETING/DELETED rows and is idempotent under outbox replay.

Run BOTH bounded commands regularly, using the same code, DB and storage config:

```bash
uv run python manage.py reconcile_private_files --batch-size 100
uv run python manage.py dispatch_outbox --batch-size 100
# Add --extra storage after 'uv run' in S3 deployments.
```

Reconciliation queues one batch (1-1000) of expired pending manifests, ownerless
ready files and due deletion tombstones. The dispatcher performs actual I/O.
`PRIVATE_FILE_PENDING_SECONDS` and `PRIVATE_FILE_RECHECK_SECONDS` both default to
3600 and allow 1-86400. Choose pending expiry well above expected bounded upload
latency. User deletion SET_NULLs ownership so cleanup does not lose object keys.

A remote PUT can finish after a timeout or process crash and after an earlier
DELETE. Periodic tombstone rechecks handle that late write. Tombstones retain UUID,
owner/purpose/time and cleanup scheduling; filename/type/hash/size are scrubbed
after physical deletion. They are NOT automatically purged. This trades recurring
metadata/outbox/delete work for recoverability. Establish a retention strategy
only after defining an upper bound on in-flight work, provider inventory and
reconciliation guarantees; arbitrary tombstone deletion can orphan late writes.

Outbox retry/backoff/dead-letter behavior is unchanged from Phase 9. DELETING rows
with dead-lettered events require operator remediation and explicit
`requeue_outbox <event-uuid>`; reconciliation does not silently override dead-letter
policy. Monitor pending age, deleting age, dead letters, tombstone growth, object
storage/temp-disk usage and `private_file_cleanup_schedule_failed` /
`private_file_storage_unavailable` log events. Published outbox retention is also
an operational responsibility. Do not delete manifests directly or clear buckets.

## Business-module boundary

Keep a business object's attachment relation and its authorization in the owning
module. A future module must check both business-object permission and file
ownership/purpose before attaching; private UUIDs are identifiers, not access
capabilities. No generic foreign key or duplicate role model is introduced here.
Use new immutable IDs for replacement and update the business relation using a
product-specific transaction plan. Upload must finish before that transaction;
files that were successfully uploaded but never attached are not distinguishable
without a business-owned attachment/expiry policy. Do not call this complete
business-document lifecycle management.

## Verification and rollout

CI runs the unchanged baseline gates plus the new tests and storage image. A
separate storage job uses real PostgreSQL/filesystem and a disposable MinIO server
built from pinned upstream source `RELEASE.2025-10-15T17-29-55Z`. It tests private
unsigned denial, metadata, signed downloads, signature tampering, API download,
replacement and physical cleanup. This is real S3-compatible integration, NOT
proof that every AWS IAM/TLS/versioning deployment has been validated. MinIO is a
loopback-only test fixture here, not a production provider recommendation.

The main suite skips three S3 tests when TEST_S3_ENDPOINT_URL is unset; enabled
integration requires dependencies and fails if they are missing. Its credentials
must allow creating/deleting a unique disposable test bucket. Never use production
credentials. Fault tests explicitly use mocks; they are separate from backend
integration evidence.

```bash
# With disposable PostgreSQL + S3-compatible server and test credentials:
TEST_S3_ENDPOINT_URL=http://127.0.0.1:9000 uv run --extra storage pytest \
  tests/test_private_files.py tests/test_private_file_edges.py \
  tests/test_private_storage_config.py tests/test_private_files_s3.py
```

Before rollout, apply migrations, test the actual reverse-proxy limits and private
bucket policy, run cleanup with the deployment identity, and verify backup/restore
of both manifests and objects. Keep signed URLs off initially. For rollback, stop
new uploads and drain/reconcile cleanup with compatible Phase 11 code; do not
blindly reverse the migration or point old code at a new public media directory.

References: [OWASP file-upload guidance](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html),
[Django upload handlers](https://docs.djangoproject.com/en/6.1/topics/http/file-uploads/),
[django-storages S3](https://django-storages.readthedocs.io/en/stable/backends/amazon-S3.html),
[upstream fixture release](https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z).
