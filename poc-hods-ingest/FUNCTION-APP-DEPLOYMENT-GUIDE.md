# Deploying an Azure Function App for an Ingest Pipeline — A Conceptual Guide

This document is deliberately **generic and conceptual** — it explains how to
deploy *any* Azure Function App for an ingest-style workload (read from a
source, write to blob storage), independent of this specific project's
infrastructure files. If you want the exact, already-deployed setup for
*this* project, see `RUNBOOK.md` and `infra/main.bicep` instead. This guide
is for understanding the *why* behind each step and what permission you
need at each point — useful if you ever have to build one from scratch, or
just want to understand what already exists.

## How an Azure Function App actually works

Before the deployment steps below, it helps to understand what a Function
App actually *is* under the hood — what's a resource vs. what's just a
file, and what each piece is doing.

### What it is

A Function App is Azure's "serverless" compute service: you give it code
and a *trigger* (a timer, an HTTP request, a new file appearing somewhere,
etc.), and Azure runs your code whenever that trigger fires — starting up
a small sandboxed execution environment behind the scenes, running your
function, then tearing it down (or keeping it warm for a bit, depending on
plan). You never provision or manage a VM yourself; Azure does that
invisibly. The Function App *resource* you see in the Portal is really
just the control surface — its identity, its name, its settings, its
billing/scale plan — not the actual running process.

### How it's provisioned

Creating a Function App (via Portal, CLI, or an IaC template like Bicep)
does three things at once, because a Function App on its own can't run:
1. Creates the `Microsoft.Web/sites` resource with `kind: functionapp` —
   this is the "Function App" you see and click on in the Portal.
2. Attaches it to a **hosting plan** (`Microsoft.Web/serverfarms`) — this
   decides what compute tier it runs on (Consumption, Premium, Dedicated).
3. Wires it to a **storage account** via the `AzureWebJobsStorage` setting
   — required, not optional, because the runtime itself needs somewhere to
   keep trigger checkpoints and internal locks.

None of this involves your actual code yet — provisioning only creates the
*host*. The code is uploaded separately, as a deployment step (see below).
This is why "the platform engineer set up the Function App for me" and
"my code is running" are two different milestones — the first one only
gets you an empty, running shell.

### How code gets uploaded

Your code lives in a **deployment package** — for Python, this is your
project folder (`function_app.py`, `host.json`, `requirements.txt`, etc.)
zipped up. Azure doesn't care how that zip gets to it; all of these
mechanisms ultimately do the same thing:

| Method | What happens |
|---|---|
| `func azure functionapp publish` (Core Tools) | Builds the zip locally, installs dependencies, uploads it for you |
| `az functionapp deployment source config-zip` (Azure CLI) | You build the zip yourself, CLI just uploads it |
| VS Code Azure Functions extension | Same as Core Tools, but driven from the editor UI |
| CI/CD pipeline (Azure DevOps, GitHub Actions) | Automates the same zip-and-upload step on every commit |

Under the hood, Azure either extracts that zip onto the Function App's
filesystem, or — more commonly today — mounts the zip directly and runs
your code straight out of it ("Run From Package"), which is faster and
avoids partial-deploy states. Either way, **uploading code never touches
the resources provisioned above** — the Function App, plan, and storage
account stay exactly as they were; only the code running inside changes.

### How your code is structured for Azure to find it

Two files tell Azure how to treat your code:
- **`host.json`** — app-wide settings for the whole Function App (timeouts,
  logging, extension versions). One per project, not per function.
- **`function_app.py`** (Python v2 programming model) — each function is a
  plain Python function decorated with its trigger, e.g.
  `@app.timer_trigger(...)`. Azure's runtime scans this file at startup to
  discover what functions exist and what triggers them — you don't
  register functions anywhere else.

### How environment variables / app settings actually work

What you see in the Portal under **Configuration → Environment variables**
(or "Application settings" in older Portal versions) is a flat list of
key/value pairs stored as part of the Function App resource itself —
*not* inside your code or your deployment package. At runtime, Azure
injects every one of these as a literal OS environment variable into the
sandbox your code runs in — which is why Python code reads them with
`os.environ.get("SOME_SETTING")` or `os.getenv(...)`: as far as your code
is concerned, they're indistinguishable from any other environment
variable on a normal machine.

A few settings are special, reserved names the *runtime itself* reads, not
your code:
- `AzureWebJobsStorage` — the runtime storage connection string from
  provisioning, above.
- `FUNCTIONS_WORKER_RUNTIME` — tells Azure which language stack to boot
  (`python`, `node`, `dotnet`, etc.).
- `FUNCTIONS_EXTENSION_VERSION` — which major version of the Functions
  runtime to use.

Everything else (`BLOB_STORAGE_CONNECTION_STRING`, `SHAREPOINT_CLIENT_ID`,
etc.) is just your own application config — Azure doesn't interpret these,
your code does.

**Secrets as Key Vault references:** instead of putting a raw secret value
directly into a setting, you can set the value to
`@Microsoft.KeyVault(SecretUri=https://<vault>.vault.azure.net/secrets/<name>)`.
Azure recognizes this special syntax and, using the Function App's managed
identity, fetches the real secret value from Key Vault and injects *that*
as the environment variable instead — your code never sees the
`@Microsoft.KeyVault(...)` string itself, only the resolved value. This
needs the **Key Vault Secrets User** role granted to the identity (see
Step 5 below) — without it, the setting will show an error instead of
resolving.

**Changing a setting takes effect on the next cold start**, not instantly
mid-execution — if a function is actively warm/running, it may need a
restart (Azure usually does this automatically when you save settings in
the Portal) before it picks up a changed value.

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

## How many storage accounts do you actually need?

**The minimum is one.** A single storage account can serve both roles —
runtime (`AzureWebJobsStorage`) and target (where your ingested files end
up) — at the same time, just using different containers inside it. This
project does exactly that: one storage account, with the runtime's own
auto-created containers sitting alongside a separate `ingest-output`
container that holds your actual data. For a single, simple
timer-triggered ingest pipeline like this one, **one storage account is
sufficient and is the recommended default** — fewer resources to manage,
fewer places to check when something goes wrong.

**When you'd provision a second one** — none of these apply to this
project, but they're the usual reasons teams split it into two accounts:
- **Isolation/governance** — keeping your own ingested data in an account
  with different access policies, retention rules, or lifecycle
  management than the runtime's internal bookkeeping data.
- **Multiple Function Apps sharing infrastructure** — if several unrelated
  Function Apps live in the same resource group, teams sometimes give each
  its own runtime storage account to avoid one app's trigger checkpoints
  competing with another's, though this isn't strictly required.
- **Durable Functions** — if you ever add orchestration (Durable
  Functions), its task hub data can get heavy; some teams move that to its
  own storage account to keep it from crowding the same account as
  ordinary blob output.
- **Different redundancy/performance tiers** — e.g., the target data needs
  geo-redundant storage (GRS) for compliance, but the runtime bookkeeping
  data doesn't need that level of durability, so splitting them saves cost.

**Bottom line for this app specifically:** if you're checking a resource
group and see only one storage account dedicated to this Function App,
that's correct and expected — it's not a missing piece. If you see
*multiple* storage accounts in the resource group, the extra ones almost
certainly belong to other components (AI Search, OpenAI, a Static Web App,
etc. — see `infra/main.bicep`'s full-stack template), not this ingest
pipeline. Identify the right one by tracing the Function App's
`AzureWebJobsStorage` / `BLOB_STORAGE_CONNECTION_STRING` settings back to
a Key Vault secret, rather than guessing by name.

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
