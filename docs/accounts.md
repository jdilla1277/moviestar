# Accounts from the CLI

Local editing needs no account. Hosted videos use a human-owned account and a
revocable grant for each CLI installation.

## Return to the website with an email link

```sh
moviestar account open
moviestar account open --email-link human@example.com --dry-run
moviestar account open --email-link human@example.com
```

Plain `account open` opens the account page in your browser. With `--email-link`,
the command asks the hosted service to email a short-lived sign-in link for an
existing account. It returns JSON immediately after the service acknowledges
the request; it does not open a browser or receive the login link. The human
opens the email and chooses **Continue with email link**, completing any extra
verification in their browser. Keep that email and its link out of agent input,
project files and logs.

`status: "email_link_requested"` and `delivery: "unconfirmed"` describe a neutral
request receipt. They do not prove that the address has an account, delivery
succeeded, a browser signed in or an installation was approved. The output masks
the address. The command does not change local account state or access the OS
credential store.
`--dry-run` reports the selected service, request UUID and optional claim UUID
without making a network request or sending mail.

Keep the returned `request_id` after a timeout or `request_unconfirmed` result.
Retry the same email and claim using `--request-id` with that UUID so a request
that reached the service does not send duplicate emails. A 429 result includes
`retry_after_seconds`; wait before retrying. Reusing an accepted request UUID
returns its earlier receipt without another email. For a fresh email after an
expired/used link or a lost message, invoke the command again without
`--request-id` after the cooldown. Changing the email or claim under a reused
UUID returns `request_conflict`.

A live pending claim for the same email and service is included automatically,
so browser sign-in can return to its installation approval page. You can also
pass that claim's public request UUID using `--claim-id`. The server checks the
recipient and claim status; a claim UUID itself gives no access. Expired claims
or claims for another email/service are not automatically attached. Browser
sign-in remains separate from installation connection: approve the requesting
installation explicitly, then use `moviestar account status` to check it.

The email request uses the service selected by
`MOVIESTAR_ACCOUNT_INSTALLATION_URL`, then `MOVIESTAR_ACCOUNT_CLAIM_URL`, then
the remembered profile's service, defaulting to `https://trymoviestar.com`.
Only that origin is used with `/api/v1/account/sign-in-links`; HTTPS is required
outside loopback. `MOVIESTAR_ACCOUNT_URL` controls the plain browser-opening
command. Use a separate `MOVIESTAR_ACCOUNT_STATE` profile for Preview testing.
If the service has not enabled email requests, the command returns `unavailable`
and exits unsuccessfully. It never silently substitutes a browser-opening action
for an email request.

## Terminal signup and sign-in

When the service has enabled terminal setup:

```sh
moviestar account connect human@example.com --terminal
moviestar account verify --code-stdin
```

The human enters the emailed code on stdin, followed by Enter.
In an interactive terminal, `moviestar account verify` uses a hidden prompt
instead. Do not put codes or passwords in command arguments, project files, or
logs. The email code is short-lived; `moviestar account resend` requests another
code subject to the authentication provider's limits. The account request can
remain valid longer than an individual email code.

If the provider's signup or sign-in attempt has expired or been abandoned,
restart verification explicitly:

```sh
moviestar account connect human@example.com --terminal --restart
```

This sends a new code and replaces the temporary provider state. While the
installation claim is still valid, its request ID, private challenge, and
installation remain the same. Enter the new code and complete verification
and approval again; restarting never grants installation access by itself.
Use `account resend` for an expired email code on a still-active attempt.

After verification, the command returns `approval_required` with the requesting
installation, terms version, statement, and policy links. Ask the human to review
these and choose a public handle. With their explicit confirmation:

```sh
moviestar account verify --handle human \
  --accept-terms hosted-terms-v1 --age-18-or-older --approve-installation
```

Use the version actually returned by the service. An agent must not infer age,
accept terms, or approve an installation without the human's instruction.
Verification alone does none of these. A changed policy requires review again.
An existing account that already accepted current consent needs only
`--approve-installation`; its handle is preserved.

If a new handle is taken, invalid, or reserved, the CLI returns a specific
`handle_taken` or `invalid_handle` code with `field: "handle"` and recovery
instructions. Choose another handle and repeat `account verify` with the new
`--handle` and the same consent/installation options. Your verified email
session is kept, so this does not require a new email code.

If signup requires a password, the result lists `password` under
`missing_fields`. Set it without putting it in command history:

```sh
moviestar account verify --password-stdin
```

Clerk remains the authentication authority. If an existing account requires
MFA, complete it using `account verify --second-factor totp --code-stdin` or
`--second-factor backup_code --code-stdin` as appropriate. Other authentication
requirements remain enforced; the CLI does not bypass them. Terminal setup
requires a service configured for email-code signup and sign-in. The browser
invitation path remains available through `account connect EMAIL`.

Successful setup returns `connected` immediately. The device credential uses
the OS credential store, with an announced private-file fallback when unavailable.
Temporary authentication state has user-only file permissions and is discarded
when setup finishes. `account status` checks the grant; `account disconnect`
revokes it. The owner can also revoke installations on their account page.

## Current plan and capacity

```sh
moviestar account status
```

For a connected installation, this reads current account information from the
service. `status: "connected"` describes the installation grant. When the service
provides a supported plan response, `plan_status: "ok"` and `plan` describe the
finished-video allowance:

| Field | Meaning |
| --- | --- |
| `plan.plan` | The current plan, such as `free` or `plus` |
| `plan.limits` | Maximum stored videos, total bytes, and bytes per video |
| `plan.usage` | Stored and pending counts/bytes, and combined usage |
| `plan.remaining` | Remaining slots and bytes after pending reservations |
| `plan.upload` | Whether capacity permits another upload, the reason, and the maximum file size that currently fits |
| `plan.billing` | Reported payment status, cadence, renewal/paid-through dates, and scheduled changes |
| `plan.lifecycle` | Reported grace, pause, and deletion dates when applicable |
| `plan.observed_at` | When this capacity snapshot was read |

All sizes use decimal units: 1 GB is 1,000,000,000 bytes. These allowances cover
hosted finished videos; Cloud project source storage and compute are separate.
The service remains responsible for quota admission, consent, access, and
lifecycle enforcement. Reading status reserves no capacity and changes no
billing. Use `moviestar account open` to manage the account with the human.
Paid-plan availability and purchase are controlled by the service.

Older services or unavailable/unsupported plan responses produce
`plan_status: "unavailable"` and `plan: null` while a valid installation stays
connected. Unknown capacity is not reported as Free. On the first successful
approval exchange, a subsequent status read may be needed to obtain the plan.
Plan snapshots are not saved in the local connection file; ordinary upload/share
dry-runs stay offline and do not claim live capacity.

## Existing accounts and hosted consent

Review the current account-wide age affirmation and policies:

```sh
moviestar account terms
moviestar account terms --accept hosted-terms-v1 --age-18-or-older
```

The read command changes nothing. Acceptance requires both the displayed terms
version and the explicit age confirmation. It applies across hosted videos;
there is no approval checkbox for each video. Existing accounts that have not
made this affirmation can remain connected, but hosted uploads return
`consent_required` until account setup is complete. Already stored videos are
preserved. Uploads stay private; this does not publish a share link.
