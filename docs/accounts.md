# Accounts from the CLI

Local editing needs no account. Hosted videos use a human-owned account and a
revocable grant for each CLI installation.

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
