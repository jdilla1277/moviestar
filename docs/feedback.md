# Feedback and account contact

Submit feedback only when the user requests or approves it. Printing a template
does not submit anything or read the saved account connection:

```bash
moviestar feedback --template
moviestar feedback --quick "The caption preview was helpful"
```

When connected, submission uses the CLI's existing approved Moviestar account
connection. The service checks the current installation authorization and
resolves that account's verified primary email, another verified account email,
or an authorized account reference if no verified email is available. Account
contact is resolved on the service; the agent and CLI do not discover email
addresses from the device, browser profiles, address books, or other applications.
No new provider permission is requested.

Only the recorded account service origin and its `/api/feedback` route can
receive the installation credential. Feedback does not follow redirects. A
different or custom endpoint cannot receive the saved credential: reconnect to
the intended service or explicitly choose anonymous submission. Authentication
and provider failures do not cause an anonymous retry.

## Anonymous reports and deliberately supplied contact

```bash
moviestar feedback --anonymous --quick "A short unlinked note"
moviestar feedback --anonymous --file feedback.md
```

`--anonymous` skips account-state and credential access. Logged-out submissions
also remain anonymous unless the user deliberately supplies a follow-up address.
An anonymous report must leave the optional follow-up email section blank.

For a guided report, save `moviestar feedback > feedback.md`, fill it in, then run
`moviestar feedback --file feedback.md`. Only that selected report is read. An
optional follow-up email is stored separately as a user-supplied alternative;
it is not treated as a verified account address. The submitted text is exactly
what the user chooses to share, so review it for private details first.

## Receipts and follow-up

The JSON receipt reports `account_linked` and `contact_available` for an
authenticated submission. It does not return the contact address, account ID,
or credential. The service's protected triage reads retain contact and its
provenance; those separate contact fields are omitted from Discord notifications.
No project files, logs, command history or device profile data are automatically
attached to feedback.

Contact capture does not enroll anyone in a newsletter or automatically send a
follow-up message. A failed or ambiguous receipt may include `feedback_id`; keep
that identifier and do not automatically resend a report that may already have
been received. If an account-linked request times out or its receipt is invalid,
`anonymous: null` means anonymity is unconfirmed; the report may already have
been stored with account contact. Older services that do not confirm account linkage are reported
as such rather than being described as authenticated success.
