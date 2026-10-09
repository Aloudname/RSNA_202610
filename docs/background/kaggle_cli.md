# Public API

### kagglehub & kaggle CLI

First things first, I need to set up Kaggle cli to download the dataset. Kaggle offers two different ways to interact programmatically with Kaggle:

- [kaggle CLI](https://github.com/Kaggle/kaggle-cli): This is a command-line interface tool for interacting via commands in a terminal or shell script ([Documentation](https://github.com/Kaggle/kaggle-cli/blob/main/docs/README.md)).
- [kagglehub](https://github.com/Kaggle/kagglehub): This is a Python library designed to allow users to interact with Kaggle resources, primarily models, datasets & competitions. It's intended for seamless integration into **Python** ML workflows ([Documentation](https://github.com/Kaggle/kagglehub/blob/main/README.md)).

### Rate Limits

Kaggle uses dynamic rate limiting on both the public API and on calls made while using the kaggle.com website. If you encounter an HTTP 429 error code or a "Too many requests" error, we recommend the following steps:

- Pause and Retry: Often, the most effective solution is to simply wait a few minutes and try your request again later.
- Review Your Logic: It is worth a quick look at your code to ensure no unintended loops or redundant calls are being triggered. This is particularly helpful when making API calls in automated scripts, where a small logic error can inadvertently lead to a high volume of requests.
- Report Platform Issues: If you have investigated your code and believe a bug on the Kaggle site is causing a request to happen more frequently than it should, please report it in the [Product Feedback](https://www.kaggle.com/discussions/product-feedback) forum so we can investigate.

### Authentication

Kaggle supports several authentication methods depending on your use case:

- **API Keys & Personal Tokens:** For automated scripts, notebook workflows, CI/CD environments, and direct SDK calls, create an API token from your [Kaggle account API tokens settings page](https://www.kaggle.com/settings/api).
- **Kaggle CLI:** For interactive command-line use, run `kaggle auth login` to authenticate via your browser.
- **OAuth 2.0 for Third-Party Applications:** For external websites, web apps, and developer tools that need to act on behalf of Kaggle users, Kaggle provides an OAuth 2.0 provider service.

When integrating Kaggle into other websites or applications, Kaggle implements the OAuth 2.0 Authorization Code flow with PKCE (Proof Key for Code Exchange). This enables third-party services to securely request granular permissions and obtain access tokens without handling user credentials directly.

The API supports:

- **Authorization Code Grant** with PKCE for public clients
- **Refresh Token Grant** for obtaining new access tokens
- **Token Introspection** (RFC 7662) for validating tokens
- **OAuth 2.0 Discovery** via well-known endpoints

#### OAuth 2.0

For public clients (CLI tools, desktop apps, etc.), the typical flow is:

1. Request a pre-configured client ID from the Kaggle team
2. Generate PKCE challenge (code\_verifier and code\_challenge)
3. Redirect user to authorization endpoint with your client details
4. User approves the authorization request on Kaggle
5. Exchange the authorization code for tokens at the token endpoint
6. Use access tokens to call Kaggle APIs
7. Refresh tokens when access tokens expire

##### Discovery Endpoints

Retrieve OAuth 2.0 server metadata including supported endpoints, grant types, and scopes:

```
GET https://www.kaggle.com/.well-known/oauth-authorization-server
```

Retrieve metadata about the protected resource (Kaggle API):

```
GET https://www.kaggle.com/.well-known/oauth-protected-resource
```

##### Client ID Types

Kaggle supports two types of OAuth clients:

| Type | Client ID Format | PKCE Required | Token Exchange Auth | Redirect URI |
| --- | --- | --- | --- | --- |
| Public Client | `<client-name>` (e.g., `gemini-cli`) | Yes | None (PKCE provides security) | Localhost only |
| Organization Client | `org:<organization-slug>` | No | HTTP Basic with org owner's [API key](/settings) | HTTPS URLs allowed |

To register a new OAuth client, contact the Kaggle team.

##### Request Body Encoding

The OAuth 2.0 token, refresh, and introspection endpoints all accept request bodies in either of two encodings:

- **`application/x-www-form-urlencoded`** — the encoding mandated by RFC 6749 §4.1.3 / §3.2 and used by default by virtually every off-the-shelf OAuth client library. Use this when in doubt; the examples in this document use it.
- **`application/json`** — also accepted as a convenience for clients that prefer to send JSON. The body must be a JSON object whose top-level keys match the parameter names listed for each endpoint.

The two encodings are interchangeable; pick whichever matches the rest of your HTTP stack. Each example below shows the form-urlencoded variant first and the equivalent JSON variant immediately after.

##### Authorization Flow

###### Step 1: Generate PKCE Challenge

Before starting the authorization flow, generate a PKCE code verifier and challenge. This step is required for public clients and should be skipped by organization clients.

```
import secrets
import hashlib
import base64

# Generate a random code_verifier (43-128 characters)
code_verifier = secrets.token_urlsafe(32)

# Create code_challenge using SHA-256
code_challenge = base64.urlsafe_b64encode(
    hashlib.sha256(code_verifier.encode()).digest()
).decode().rstrip('=')
```

###### Step 2: Start Authorization Flow

Redirect the user to the authorization endpoint:

```
GET https://www.kaggle.com/api/v1/oauth2/authorize
```

**Query Parameters:**

| Parameter | Required | Description |
| --- | --- | --- |
| `client_id` | Yes | Your registered client ID |
| `redirect_uri` | Yes | Must match a registered redirect URI |
| `scope` | Yes | Space-separated list of scopes |
| `state` | Yes | Random string (20-128 chars) for CSRF protection |
| `response_type` | Yes | Must be `"code"` |
| `response_mode` | Yes | Must be `"query"` |
| `code_challenge` | Yes\* | Base64URL-encoded SHA-256 hash of code\_verifier |
| `code_challenge_method` | Yes\* | Must be `"S256"` |

*\*Required for public clients. Must not be sent by organization clients.*

###### Step 3: User Authorization

The user is redirected to Kaggle's consent screen where they log in (if needed), review the requested permissions, optionally restrict the scopes, and approve or deny the request.

###### Step 4: Receive Authorization Code

After approval, Kaggle redirects back to your `redirect_uri` with the authorization code:

```
http://localhost:8080/callback?code=<authorization_code>&state=xyzABC123456789012345
```

**Important:** Verify that the `state` parameter matches what you sent to prevent CSRF attacks.

###### Step 5: Exchange Code for Tokens

Exchange the authorization code for access and refresh tokens:

```
POST https://www.kaggle.com/api/v1/oauth2/token
Content-Type: application/x-www-form-urlencoded
```

As described in [Request Body Encoding](#oauth-request-encoding), the same request may also be sent with `Content-Type: application/json` and a JSON body.

**Request Body Parameters:**

| Parameter | Required | Description |
| --- | --- | --- |
| `grant_type` | Yes | Must be `"authorization_code"` |
| `code` | Yes | The authorization code from the callback |
| `code_verifier` | Public clients only | Original code\_verifier used to generate code\_challenge.<br>**Must not** be sent by organization clients. |
| `client_id` | No | Your client ID |
| `redirect_uri` | No | The redirect URI used in the authorization request |

###### Client Authentication

How you authenticate the token exchange request depends on your client type:

- **Public clients** do not require any authentication header. The `code_verifier` (PKCE) proves the caller is the same party that initiated the authorization flow.
- **Organization clients** must authenticate the request using the organization owner's Kaggle API credentials via HTTP Basic authentication. The organization owner's username and [API key](/settings) should be included in an `Authorization` header. Organization clients must **not** send a `code_verifier`.

###### Public Client Example (form-urlencoded)

```
curl -X POST https://www.kaggle.com/api/v1/oauth2/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=authorization_code" \
  -d "code=<authorization_code>" \
  -d "code_verifier=dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
```

###### Public Client Example (JSON)

```
curl -X POST https://www.kaggle.com/api/v1/oauth2/token \
  -H "Content-Type: application/json" \
  -d '{
    "grant_type": "authorization_code",
    "code": "<authorization_code>",
    "code_verifier": "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
  }'
```

###### Organization Client Example (form-urlencoded)

```
curl -X POST https://www.kaggle.com/api/v1/oauth2/token \
  -u "<org_owner_username>:<org_owner_api_key>" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=authorization_code" \
  -d "code=<authorization_code>"
```

###### Organization Client Example (JSON)

```
curl -X POST https://www.kaggle.com/api/v1/oauth2/token \
  -u "<org_owner_username>:<org_owner_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "grant_type": "authorization_code",
    "code": "<authorization_code>"
  }'
```

**Response:**

```
{
  "access_token": "KGAT_...",
  "refresh_token": "KGRT_...",
  "token_type": "Bearer",
  "expires_in": 10800,
  "username": "johndoe",
  "user_id": 12345,
  "scope": "datasets.get:* models.get:*"
}
```

##### Token Management

###### Refresh Access Token

Access tokens expire after 3 hours. Use the refresh token to obtain new access tokens. Refresh token requests do not require client authentication for either client type.

###### Form-urlencoded

```
curl -X POST https://www.kaggle.com/api/v1/oauth2/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=refresh_token" \
  -d "refresh_token=KGRT_..."
```

###### JSON (equivalent)

```
curl -X POST https://www.kaggle.com/api/v1/oauth2/token \
  -H "Content-Type: application/json" \
  -d '{"grant_type": "refresh_token", "refresh_token": "KGRT_..."}'
```

###### Token Introspection (RFC 7662)

Validate and inspect tokens:

###### Form-urlencoded

```
curl -X POST https://www.kaggle.com/api/v1/oauth2/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "token=<access_token_or_refresh_token>"
```

###### JSON (equivalent)

```
curl -X POST https://www.kaggle.com/api/v1/oauth2/introspect \
  -H "Content-Type: application/json" \
  -d '{"token": "<access_token_or_refresh_token>"}'
```

Both encodings return the same JSON response. For an active token:

```
{
  "active": true,
  "username": "johndoe",
  "user_id": 12345,
  "scope": "datasets.viewer:*",
  "exp": 1705719600
}
```

For an expired, revoked, or otherwise invalid token:

```
{
  "active": false
}
```

##### Scopes

Scopes control what permissions your application has when acting on behalf of the user. Scopes follow the format: `<permission-or-role>:<resource-id-or-*>`

- Use `*` for the resource id to grant the permission across all resources of that type (e.g., `datasets.get:*`).
- Use a numeric resource id to scope to a single resource (e.g., `datasets.get:124`).

**Common Permissions:**

| Permission | Description |
| --- | --- |
| `datasets.get` | Read dataset metadata and files |
| `datasets.update` | Update existing datasets |
| `models.get` | Read model metadata and files |
| `models.create` | Create new models |
| `models.update` | Update existing models |
| `kernels.get` | Read notebooks/kernels |
| `kernels.update` | Create or update notebooks |
| `competitions.get` | Read competition metadata |
| `competitions.submit` | Submit to competitions |

**Available Roles** (bundle related permissions):

| Role | Description |
| --- | --- |
| `datasets.viewer` | Read-only access to datasets |
| `datasets.editor` | Read and write access to datasets |
| `models.viewer` | Read-only access to models |
| `models.editor` | Read and write access to models |
| `kernels.viewer` | Read-only access to notebooks |
| `kernels.editor` | Read and write access to notebooks |
| `competitions.viewer` | Read-only access to competitions |

Request multiple scopes by separating them with spaces: `datasets.get:* models.get:* kernels.get:*`

A complete reference of every permission and role accepted by the OAuth provider is provided in the [Appendix: Full Scope Reference](#oauth-scopes-appendix) at the end of this document.

##### Using Access Tokens

Include the access token in API requests using the `Authorization` header:

```
curl https://www.kaggle.com/api/v1/datasets/list \
  -H "Authorization: Bearer KGAT_..."
```

##### Error Handling

**Authorization Errors** are returned as query parameters on the redirect URI:

| Error Code | Description |
| --- | --- |
| `invalid_request` | Missing or invalid parameter |
| `invalid_client` | Unknown or disabled client |
| `invalid_scope` | Requested scope not allowed for this client |
| `access_denied` | User denied authorization |

**Token Endpoint Errors** return JSON with HTTP status 400:

| Error Code | Description |
| --- | --- |
| `invalid_request` | Missing or invalid parameter |
| `invalid_grant` | Invalid, expired, or revoked authorization code |
| `invalid_client` | Unknown client ID |

##### Security Considerations

- **Always use HTTPS** for non-localhost redirect URIs
- **Validate the state parameter** to prevent CSRF attacks
- **Store refresh tokens securely** - they provide long-lived access
- **Use minimal scopes** - only request permissions your application needs
- **PKCE is mandatory** for public clients to prevent authorization code interception
- **Access tokens expire** after 3 hours - use refresh tokens to obtain new ones
- **Organization clients must authenticate token exchange requests** using the organization owner's [API credentials](/settings) via HTTP Basic authentication. Keep these credentials secure on your server - never expose them in client-side code.

##### Appendix: Scope Reference

The tables below cover the high-level CRUD permissions and roles that most applications will need across Kaggle's primary resource types: datasets, models, kernels (notebooks), competitions, forums, and benchmarks, plus site-wide roles. Additional fine-grained permissions exist (e.g. for voting, tagging, IAM policy management, sub-resource versions); contact the Kaggle team if your application needs a permission not listed here.

Note: granting a scope only entitles the bearer to act on resources the underlying user already has access to. Site-wide administrative scopes (e.g., `resources.admin`) cannot elevate a user beyond their existing IAM permissions.

###### Permissions

**Datasets**

| Permission | Description |
| --- | --- |
| `datasets.get` | Read dataset metadata and files |
| `datasets.update` | Update existing datasets |
| `datasets.delete` | Delete datasets |
| `dataset_versions.create` | Create new dataset versions (also used to create new datasets) |

**Models**

| Permission | Description |
| --- | --- |
| `models.get` | Read model metadata and files |
| `models.create` | Create new models |
| `models.update` | Update existing models |
| `models.delete` | Delete models |

**Kernels (Notebooks)**

| Permission | Description |
| --- | --- |
| `kernels.get` | Read notebooks |
| `kernels.update` | Create or update notebooks |
| `kernels.delete` | Delete notebooks |
| `kernels.execute` | Execute notebooks |

**Competitions**

| Permission | Description |
| --- | --- |
| `competitions.get` | Read competition metadata |
| `competitions.update` | Update existing competitions |
| `competitions.download_data` | Download competition data files |
| `competitions.participate` | Join competitions |
| `competitions.submit` | Submit to competitions |
| `submissions.get` | Read competition submissions |

**Forums**

| Permission | Description |
| --- | --- |
| `forum_topics.get` | Read forum topics |
| `forum_topics.create` | Create new forum topics |
| `forum_topics.update` | Update forum topics |
| `forum_topics.delete` | Delete forum topics |
| `forum_messages.get` | Read forum messages (replies) |
| `forum_messages.create` | Post forum messages |
| `forum_messages.update` | Edit forum messages |
| `forum_messages.delete` | Delete forum messages |

**Benchmarks**

| Permission | Description |
| --- | --- |
| `benchmarks.get` | Read benchmark metadata |
| `benchmarks.list` | List benchmarks |
| `benchmarks.create` | Create new benchmarks |
| `benchmarks.update` | Update existing benchmarks |
| `benchmarks.delete` | Delete benchmarks |

###### Roles

Roles bundle related permissions. `viewer` grants read access, `editor` grants read and write access, and `admin` grants full management access (including delete and IAM changes).

**Per-resource roles**

| Role | Description |
| --- | --- |
| `datasets.viewer` | Read-only access to datasets |
| `datasets.editor` | Read and write access to datasets |
| `datasets.admin` | Full management of datasets |
| `models.viewer` | Read-only access to models |
| `models.editor` | Read and write access to models |
| `models.admin` | Full management of models |
| `kernels.viewer` | Read-only access to notebooks |
| `kernels.editor` | Read and write access to notebooks |
| `kernels.admin` | Full management of notebooks |
| `competitions.viewer` | Read-only access to competitions |
| `competitions.participator` | Join and submit to competitions |
| `competitions.editor` | Read and write access to competitions |
| `competitions.admin` | Full management of competitions |
| `forum_topics.viewer` | Read-only access to forum topics |
| `forum_topics.editor` | Create and update forum topics |
| `forum_topics.admin` | Full management of forum topics |
| `forum_messages.viewer` | Read-only access to forum messages |
| `forum_messages.participator` | Post and react to forum messages |
| `forum_messages.editor` | Create and update forum messages |
| `forum_messages.admin` | Full management of forum messages |
| `benchmarks.viewer` | Read-only access to benchmarks |
| `benchmarks.editor` | Read and write access to benchmarks |
| `benchmarks.admin` | Full management of benchmarks |

**Site-wide**

| Role | Description |
| --- | --- |
| `resources.viewer` | Read-only access across all resource types the user can see |
| `resources.editor` | Read and write access across all resource types the user can edit |
| `resources.admin` | Full management access across all resource types the user can administer |