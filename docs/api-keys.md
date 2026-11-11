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

The key supports an account check through HTTP or remote MCP. For HTTP:

```sh
curl --fail-with-body https://trymoviestar.com/api/v1/account/me \
  -H "Authorization: Bearer $MOVIESTAR_API_KEY"
```

Set `MOVIESTAR_API_KEY` securely in your shell or secret manager. The response
identifies your account handle and advertises `account:read`. A revoked key
returns HTTP 401 immediately. [Cloud project creation and metadata](cloud-projects.md#create-through-the-http-api)
additionally require personal Cloud access on the service. Personal keys do not
grant media, editing or render API access and are separate from partner
integration keys and CLI installation credentials.

The website/API deployment and the CLI release are separate. An older installed
CLI may lack `account keys`; use the website URL directly in that case.

## Authenticate a remote MCP client with your key

For an integration or agent runtime that supports custom HTTP headers, use the
same personal key with `https://trymoviestar.com/mcp`:

```http
Authorization: Bearer YOUR_MOVIESTAR_API_KEY
```

Supply the header through your client configuration or secret manager. It is
not a tool argument and should not appear in the model's prompt. API-key mode
uses no interactive OAuth flow after key creation. ChatGPT's account connection
continues to use OAuth; it does not take a personal API key.

For example, using the official MCP TypeScript SDK in a Node.js application
(example tested with `@modelcontextprotocol/sdk` 1.32.2):

```javascript
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const key = process.env.MOVIESTAR_API_KEY;
if (!key) throw new Error("Set MOVIESTAR_API_KEY in your environment.");

const client = new Client({ name: "my-agent", version: "1.0.0" });
const transport = new StreamableHTTPClientTransport(
  new URL("https://trymoviestar.com/mcp"),
  { requestInit: { headers: { Authorization: `Bearer ${key}` } } },
);

try {
  await client.connect(transport);
  const result = await client.callTool({
    name: "moviestar_account",
    arguments: {},
  });
  console.log(result.structuredContent);
} finally {
  await client.close();
}
```

Expected result: `{ "status": "connected", "handle": "@your_handle" }`.
Use this from your backend or agent runtime rather than embedding a key in
browser code. Do not configure an OAuth provider for this API-key connection.
The account is the one that created the key; a key cannot select another owner.

Revoke the key on the website, then repeat the call using the same client.
The next protected request must receive HTTP 401. The same revocation ends API
and MCP access; deleting the revoked key record does not restore either.

The account check advertises `account:read`. On services with personal Cloud
projects enabled, the same key also authorizes
[the project creation tool](cloud-projects.md#create-through-hosted-mcp).
It does not unlock media/editing/render tools. OAuth discovery and ChatGPT
linking remain available alongside this developer-controlled mode.

For staging tests, create the key on that staging website and use its exact
`/mcp` endpoint. An invalid or revoked key returns a website key-settings hint;
it does not ask the developer to complete a Clerk OAuth login. A temporary
account-verification outage returns HTTP 503; retry without replacing the key.
