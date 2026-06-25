# Deploying an Azure Function App for an Ingest Pipeline — A Conceptual Guide

This document is deliberately **generic and conceptual** — it explains how to
deploy *any* Azure Function App for an ingest-style workload (read from a
source, write to blob storage), independent of this specific project's
infrastructure files. If you want the exact, already-deployed setup for
*this* project, see `RUNBOOK.md` and `infra/main.bicep` instead. This guide
is for understanding the *why* behind each step and what permission you
need at each point — useful if you ever have to build one from scratch, or
just want to understand what already exists.

## The big picture

A Function App that ingests files and writes them to Blob Storage is made of
a handful of Azure resources working together, plus one identity concept
that ties them together securely:

```
Resource Group
├── Storage account #1 (runtime)  ──┐
│                                    ├── used by the Function App's plumbing
├── App Service Plan                ┘    (triggers, logs, locks)
├── Function App  ───── managed identity ─────┐
│                                              ├── used to authenticate to:
├── Key Vault            <───────────────────┤
└── Storage account #2 (target)  <───────────┘
```

Two of those storage accounts can be the *same* account (this project does
that) or two *different* accounts — more on that distinction below.

## Step-by-step

### Step 1 — Decide on a storage account for the runtime

Every Function App needs exactly one storage account wired in as
`AzureWebJobsStorage`. This is **not** where your ingested files go — it's
internal plumbing the Functions runtime uses for:
- Trigger checkpoints (e.g., remembering where a queue/timer trigger left off)
- Lease blobs (so a timer trigger doesn't fire twice if you scale to more
  than one instance)
- Function keys and deployment metadata
- Some logging

You can create a new storage account for this or reuse an existing one.

- **Permission needed:** Contributor on the resource group (or at minimum
  on the storage account, if it already exists) — creating any Azure
  resource requires Contributor or higher.

### Step 2 — Choose a hosting plan

This decides the compute tier the Function App runs on:
- **Consumption** — pay only per execution, scales to zero when idle.
  Good default for a simple timer-triggered ingest job.
- **Premium** — pre-warmed instances, no cold start, supports VNet
  integration. Worth it if cold starts are a problem or you need private
  networking.
- **Dedicated (App Service Plan)** — runs on a fixed-size VM you're already
  paying for (e.g., if you're co-hosting other apps on the same plan).

- **Permission needed:** Contributor on the resource group.

### Step 3 — Create the Function App

At creation time you specify: name, runtime stack (Python, Node, .NET,
etc.), region, the storage account from Step 1, and the plan from Step 2.
Azure will also offer to auto-create an Application Insights instance —
accept it unless you already have one, since it's your main window into
whether the function is actually running and succeeding.

- **Permission needed:** Contributor on the resource group.

### Step 4 — Enable a managed identity

Turn on **System-assigned identity** under the Function App's **Identity**
blade. This gives the Function App its own Azure AD identity, distinct from
yours, so it can authenticate to other Azure resources (Key Vault, target
storage, etc.) **without** a secret or password embedded in code or
settings.

Why this matters: without it, you'd have to hardcode a storage account key
or a Key Vault access key somewhere — a credential that can leak. With a
managed identity, Azure handles the authentication token exchange behind
the scenes; nothing secret is ever written down.

- **Permission needed:** Contributor on the Function App (enabling identity
  is a write/configuration action).

### Step 5 — Grant the managed identity access to what it needs

This is the step people most often forget, and the most common reason a
freshly-deployed function fails silently. The identity from Step 4 starts
with *zero* permissions — it can't read anything until you explicitly grant
it access. Typical grants for an ingest pipeline:

| If your code needs to... | Grant the identity this role | On this resource |
|---|---|---|
| Read a secret (e.g., a source-system API key) | **Key Vault Secrets User** | The Key Vault |
| Write blobs using Azure AD auth (no account key) | **Storage Blob Data Contributor** | The target storage account |
| Read (not write) blobs | **Storage Blob Data Reader** | The target storage account |

Note: if you instead give the Function App a **connection string** (which
embeds an account key) rather than relying on the managed identity, you
don't need the Storage Blob Data role — the account key itself is the
credential. Using the managed identity instead of an account key is the
more secure pattern, but either works.

- **Permission needed to grant these roles:** **Owner** or
  **User Access Administrator** on the resource you're assigning the role
  on. This is a deliberate gap in Contributor — Contributor can create and
  configure resources, but cannot grant access to other identities. If
  you only have Contributor, you'll be able to do every other step in this
  guide except this one; you'll need someone with Owner/User Access
  Administrator to do this specific grant for you.

### Step 6 — Configure app settings

Add whatever configuration your code needs: connection strings, container
names, source-system identifiers, etc. Stored under the Function App's
**Configuration → Application settings**.

Two patterns for secrets specifically:
- **Key Vault reference** — the setting's value looks like
  `@Microsoft.KeyVault(SecretUri=...)`. Azure resolves it automatically at
  runtime using the managed identity from Step 4/5. The actual secret value
  never appears in the Function App's settings — only a pointer to it.
- **Plain value** — the setting just holds the literal value (e.g., a raw
  connection string). Simpler, but the secret is now visible to anyone with
  read access to the Function App's configuration, not just Key Vault.

- **Permission needed:** Contributor on the Function App.

### Step 7 — Write and deploy your code

Push your function code via CI/CD pipeline, VS Code extension, or a zip
deploy. The code reads the app settings from Step 6 to know what to
connect to and what to do.

- **Permission needed:** Contributor on the Function App.

### Step 8 — Create the target container and test

In the target storage account, create the blob container your code will
write into (if it doesn't already exist), then trigger the function (wait
for its timer, or invoke it manually) and confirm a file actually lands in
that container.

- **Permission needed:** Contributor on the target storage account (gives
  you `listKeys`, which is enough to browse the container via the Portal's
  Storage Browser using the account key) — or **Storage Blob Data Reader**
  if you're authenticating with your own Azure AD account instead of the
  account key.

## Summary — who needs what

**You, the human doing the deployment:**
- **Contributor** on the resource group for the entire build (Steps 1-4,
  6-8).
- **Owner** or **User Access Administrator**, just for the single moment in
  Step 5 where you grant the Function App's identity its own permissions.
  If you don't have this, the deployment can still finish — you'll just
  need someone else to do that one role-assignment step.

**The Function App's managed identity (not you — a separate identity):**
- **Key Vault Secrets User** on whichever Key Vault holds secrets it needs.
- **Storage Blob Data Contributor** (or **Reader**, if read-only) on the
  target storage account — only if using Azure AD auth instead of a
  connection string.

## Common pitfalls

- **Forgetting Step 5.** The Function App deploys fine, app settings look
  correct, but every run fails with an authorization error — because the
  identity was never granted access to the Key Vault or storage account it
  needs.
- **Confusing the runtime storage account with the target one.** They can
  be the same account, but they don't have to be — and when something goes
  wrong, check which one your code is actually pointed at (via its app
  settings), not just which one the Function App was created with.
- **Assuming Contributor is enough for everything.** It covers almost
  every step here except granting roles to other identities (Step 5) —
  that one specifically requires Owner or User Access Administrator.
- **Hardcoding secrets instead of using Key Vault references or managed
  identity.** Works for a quick test, but anyone with read access to the
  Function App's configuration can then see the raw secret. Prefer Key
  Vault references + managed identity for anything beyond a throwaway test.
