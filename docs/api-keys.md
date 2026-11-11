# Personal API keys

Create and manage personal keys at <https://trymoviestar.com/account/api-keys>.
Sign in to an existing Moviestar account first. Finish handle and terms setup
if requested, name your key, and select **Create key**. Copy the secret when it
appears: it is shown once. The website can list key names and revoke keys, but
cannot reveal an existing secret again. If you lose one, revoke it and create
another. Do not paste keys into chat or commit them to source control.

The CLI can open the website without requiring a connected installation:

```sh
moviestar account keys
# For an agent or terminal without browser access:
moviestar account keys --no-browser
```

These commands only return/open the website link. They do not issue or store
keys. The hosted MCP tool `moviestar_api_keys` likewise returns the website link;
ChatGPT continues to connect using OAuth, without a personal API key.

The initial key supports an account check:

```sh
curl --fail-with-body https://trymoviestar.com/api/v1/account/me \
  -H "Authorization: Bearer $MOVIESTAR_API_KEY"
```

Set `MOVIESTAR_API_KEY` securely in your shell or secret manager. The response
identifies your account handle and advertises `account:read`. A revoked key
returns HTTP 401 immediately. Personal keys do not yet authorize project,
media, editing or render APIs, and are separate from partner integration keys
and CLI installation credentials. Additional API journeys will add their own
supported permissions.

The website/API deployment and the CLI release are separate. An older installed
CLI may lack `account keys`; use the website URL directly in that case.
