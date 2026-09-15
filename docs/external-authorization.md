# External ManageBac connection

The HTTPS service at `https://managebac.archik.tech` owns both the MCP endpoint
(`/mcp`) and its account portal (`/settings`). Life OS holds only the catalog
description, icon, resource/scope, identity tool and opaque account IDs.

The portal authenticates through the shared Archik passkey authority with a
single-use, application-bound settings ticket. Like the existing legacy MCP
settings applications, this deployment currently grants settings and MCP access
only to the Archik owner; it does not expose the owner's school data to other
Life OS users. Tenant access would require a subject-scoped vault and settings
ticket support, not simply relaxing the verifier.

ManageBac does not provide this browser connector with a provider OAuth grant.
The portal therefore collects school credentials on the **MCP server's origin**,
verifies them against the school, then encrypts them using a server-local AES-GCM
key. Clients authenticate with resource-scoped OAuth tokens, never school
passwords. Basic Auth and environment fallback are not accepted in HTTP mode.
Environment credentials remain available for the standalone sync-only CLI.

Multiple school accounts can be added, reconnected and deleted independently.
The default account is used unless an MCP tool receives `account_id`, an opaque
server-generated ID. `list_accounts` exposes safe identity metadata. Each account
has a separate SQLite cache and artifact directory. Deletion removes its encrypted
credentials and local cache without changing the account or assignments at school.

Life OS supplies an allowlisted `return_to` URL when opening the portal. On return,
the user explicitly completes the generic MCP identity/discovery check. The
`disconnect_account` MCP lifecycle operation is internal to the Life OS connection
UI and idempotent; a remote failure must not revoke the local connection.

## Deployment

1. Install the project with `[server]` extras in its dedicated virtual environment.
2. Run `python deploy/configure_auth.py` as root; it copies only the broker internal
   secret to `/etc/manageback-mcp/auth.env`, mode 0600. No credential is checked in.
3. Install the supplied systemd unit and nginx vhost into `/etc/archik-sites`.
4. Provision DNS and a certificate; include the domain in the host's certificate
   renewal/publication scripts.
5. Register the metadata-only `managebac` settings origin and OAuth resource/scope
   with the shared authority. Restart the authority and ManageBac service.
6. Run `python deploy/smoke.py` as root for read-only portal and OAuth/MCP checks.

A successful deployment smoke check does not claim a real school login has been
performed. The user must provide their school credentials in the external portal.
