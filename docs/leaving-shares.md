# Leave a share received by your account

A recipient can leave an accepted asset or project share through Web, remote
CLI, hosted MCP or API. The owner retains the original; other recipients are
unaffected. Owners cannot leave an object they own. This journey does not delete
files or decline pending invitations.

**Leave asset** ends your accepted direct shares for that asset. Access through
projects remains. The receipt's `remaining_access` reports that access, and Web
links to Shared projects so you can leave the project to remove that path. An
asset available only through a project returns `project_access_only` with that
next step. Multiple direct invitations accepted by the same account are ended
together; another recipient's grants are preserved.

**Leave project** ends your accepted project grants and the asset access inherited
through that project. Direct asset shares and independent other-project paths
survive. Project references and original files are preserved.

## Web

Open a shared asset or shared project, choose **Leave asset** or **Leave project**,
read the scope, then confirm. A successful leave with no remaining access returns
to the shared collection. If an asset still has project access, the result explains
that and links to Shared projects. Retrying after an uncertain reply uses the
same key; Web never repeats the mutation automatically.

## Remote CLI

```sh
moviestar cloud assets leave ASSET_ID
moviestar cloud projects leave PROJECT_ID
```

Use an approved recipient installation. The CLI generates and saves a retry key
before sending the write. After an uncertain result, rerun the same command or
pass the returned `--idempotency-key` explicitly. A lost/malformed reply is
`leave_unconfirmed`, never proof that access ended. Older services fail gracefully
with `unsupported_operation` before changing access.

## Hosted MCP

Use `moviestar_leave_asset` (`asset_id`, `idempotency_key`) or
`moviestar_leave_project` (`project_id`, `idempotency_key`) only on the recipient's
explicit request. Generate and retain the key before sending; preserve it and
the object ID after uncertainty. These tools are writes, not discovery actions.
They work with the authenticated OAuth account or a personal API key.

## API and retries

`POST /api/v1/account/cloud/shared-access/:kind/:id/leave` with `kind` asset or
project, an `Idempotency-Key` header, and JSON `{}`. IDs identify originals or
projects, not invitations. Personal API keys and approved installation proofs use
existing Bearer authentication. Cookie writes require the same origin.

Responses use `contract_version: recipient-leave-v1`, `status: left|already_left`,
`kind`, `resource_id`, `changed`, `remaining_access`, and `replayed`. A fresh key is
a deliberate new request. A repeated key never revokes a later re-invitation;
`remaining_access` is evaluated from current grants, so a replay may report newer
access even though the earlier leave was recorded. Review current access before
starting a new leave request. Merely changing keys is not a recovery strategy.

The service rechecks the current credential, account and resource grants in the
same transaction. Recipients can affect only their own accepted grants. Listing,
reviewing and leaving do not send invitation emails. Pending invites still use
the separate review/accept journey.
