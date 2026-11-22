# Account usage and Activity

Read the same storage and monthly Cloud allowance shown on Web Usage. These
commands use your existing approved installation; they need no local media,
FFmpeg or Cloud worker. Local jobs are free.

```sh
moviestar account status
moviestar account usage
moviestar account activity --cloud --limit 10
```

If not connected, follow [account setup](accounts.md). Use the same service and
account in the browser and CLI. These commands require compatible client and
service versions; an older service may return unavailable.

`account usage` returns JSON with `usage`, `links`, and `support`:

| Field inside `usage` | Meaning |
| --- | --- |
| `plan`, `observed_at` | Current Free/Plus plan and snapshot time |
| `period_start`, `resets_at` | Exact UTC boundaries of the current calendar month |
| `limits` | Monthly processing milliseconds, output bytes, and simultaneous jobs |
| `usage` | Settled processing/output, separately reserved amounts, and executing jobs |
| `remaining` | Remaining monthly capacity after reservations |
| `storage.limits` | Stored asset count, total bytes, and per-file byte limit |
| `storage.usage` | Stored assets/bytes and combined occupied amounts including pending uploads |
| `storage.remaining` | Available asset slots and bytes after pending uploads |

Processing milliseconds are normalized service allowance units, with the
operation's configured weight. They are not video duration, worker queue time,
or a dollar estimate. Bytes are exact integer counts (1 GB = 1,000,000,000 bytes).
Storage remains occupied until released; it does not reset each month. A
snapshot does not reserve capacity or guarantee that another job will fit.

## Explain usage with Activity

`account activity` lists logical tasks, with attempts nested inside their job.
Use `--cloud` to focus on Cloud jobs. Without it, the list also includes other
account Activity, including unmetered or historical entries.

```sh
moviestar account activity --cloud --limit 10
# If next_cursor is non-null, copy its exact value:
moviestar account activity --cloud --limit 10 --cursor COPIED_NEXT_CURSOR
```

Pages contain at most 50 tasks, each with up to 20 recent attempts and a total
`attempt_count`. Results include status, timestamps, current owned file/project
names when available, and `cloud_usage` when an existing allowance record is
linked. Names may be null after deletion or when unavailable. Failed or
cancelled work can have real provider costs while its monthly reservation is
released. Historical entries without an allowance record are not evidence of
zero current usage. Use `account usage` for authoritative monthly totals.

`actual_cost_usd` is a decimal string when evidenced and null when unknown.
These provider operating costs are **not customer charges**. Do not add attempt
rows to parent usage, infer dollars from elapsed time, or replace null with zero.

Reading, paging, reopening or following a download link schedules no processing,
creates no saved edit, and adds no charge. There are no automatic overage charges.

## Web and support handoffs

Both commands return `links.usage_url`, `links.activity_url`, and
`links.manage_assets_url` on the connected service. The browser may require its
own sign-in. `support.email` and `support.url` let you ask for more flexibility;
opening the mailto link opens a composer and does not send email.

These reads do not purchase a plan, change payment information, cancel a plan,
or send support mail.

## Hosted MCP and HTTP

On a compatible hosted MCP server, use:

- `moviestar_get_usage` with `{}`.
- `moviestar_list_activity` with `{ "type": "cloud", "limit": 10 }`.
  Pass the returned `next_cursor` as `cursor` for another page.

Sign in through the server's OAuth flow, or authenticate a developer-controlled
MCP client with a [personal API key](api-keys.md). Tool metadata is public;
account results require current authorization. Refresh your MCP connection's
tool metadata after a server update.

Personal API-key HTTP clients can use:

```http
GET /api/v1/account/usage
Authorization: Bearer YOUR_PERSONAL_API_KEY
```

```http
GET /api/v1/account/activity/tasks?type=cloud&limit=10
Authorization: Bearer YOUR_PERSONAL_API_KEY
```

CLI installation requests use the existing installation transport and credential
store. Never put credentials in command arguments or URLs. Clients cannot select
an account or override the clock; the server derives identity and monthly period.

## Recovery

A failed read exits unsuccessfully with a structured error. Missing or malformed
usage is unavailable, never a Free plan or a zero balance.

- `credential_invalid`: check `account status`; reconnect or replace a revoked
  key with the human's approval.
- `account_setup_required` / `account_required`: finish setup on the website.
- `account_consent_required`: read `account terms` and ask the human to review
  the current terms. Do not accept for them.
- `invalid_request`: use limits 1–50 and the exact returned cursor; omit account,
  date and unsupported filter arguments.
- `account_unavailable`: retry later. Keep the existing connection.
- `invalid_response`: use compatible client/service versions; no balance was
  inferred from the incomplete response.

## Estimated compute cost

On compatible services, screenshot Activity has `estimated_compute` on the job
and its attempts. `status: estimated` includes `amount_usd` (with the configured
adjustment) and `base_usd` (before adjustment), as exact USD strings. Attempts
include their pinned `policy_version` and `adjustment_multiplier`; the job sums
all its attempts, including failures and retries, once. Do not add that total
and the attempt amounts together.

Pending or unavailable estimates have null amounts and an explicit `reason`.
Missing worker pricing does not mean zero. Other job families are not covered
by this first integration. Local reads and metadata edits remain unmetered.

The method is single-slot worker occupancy, measured from attempt claim to its
terminal timestamp, not video duration or weighted allowance milliseconds.
This is an estimate of compute only: storage, transfer, database/API, idle host
cost and billing minimums/credits/discounts outside the configured rate are not
included. It is not an AWS invoice or a customer charge. Actual provider costs
remain in their separate receipt fields and may arrive later.

Estimates persist at completion; polling does not calculate new work or change
money. A newly configured price applies to new attempts; existing estimates
retain their original rate and adjustment. Future reconciliation can compare
these original estimates with provider evidence without issuing a new charge.
