# Deploying an Azure Function App for an Ingest Pipeline — A Conceptual Guide

This document is deliberately **generic and conceptual** — it explains how to
deploy *any* Azure Function App for an ingest-style workload (read from a
source, write to blob storage), independent of this specific project's
infrastructure files. If you want the exact, already-deployed setup for
*this* project, see `RUNBOOK.md` and `infra/main.bicep` instead. This guide
is for understanding the *why* behind each step and what permission you
need at each point — useful if you ever have to build one from scratch, or
just want to understand what already exists.

## Meeting checklist — what to confirm with your platform engineer

Use this as a literal walk-through during the meeting. For each item,
either get a yes/name from them, or flag it as missing/TBD. It's ordered
so that earlier items unblock later ones — don't skip ahead.

**1. Resources that must exist**
- [ ] One **Function App** (kind = Function App, not a Web App — confirm
  by asking "does it have a Functions blade?")
- [ ] One **hosting plan** behind it (Consumption is fine for a
  timer-triggered job — ask which tier they used)
- [ ] One **storage account** wired in as `AzureWebJobsStorage` (this can
  double as your target storage — see "How many storage accounts" below;
  you do not need them to provision a second one for this app)
- [ ] One **Key Vault** (if any secrets — e.g. a source-system client
  secret — are meant to be stored there rather than as plain app settings)
- [ ] Application Insights (ask them to confirm it's linked — this is your
  only window into logs once it's running)

**2. Identity and permissions on the Function App**
- [ ] **System-assigned managed identity** is turned on (Function App →
  Identity blade → Status: On)
- [ ] That identity has been granted **Key Vault Secrets User** on the Key
  Vault, if you're using Key Vault references for any secret
- [ ] That identity has been granted whatever role it needs on the target
  storage account, *if and only if* you're using Azure AD auth for blob
  access instead of a connection string (most simple setups use a
  connection string instead — confirm which one applies here)

**3. Access for you, the developer**
- [ ] You have **Contributor** on the Function App itself (minimum to view
  /edit app settings, redeploy code, trigger test runs)
- [ ] You have **Contributor** on the storage account that backs this app
  (lets you browse the container via the Portal's Storage Browser)
- [ ] You have **Key Vault Secrets User** on the Key Vault, if you'll ever
  need to read a secret value yourself (e.g. to confirm which storage
  account a connection string points to)
- [ ] Ask **how** these will be granted — directly to your account, or by
  adding you to a group. If it's a group, ask which one, so you know who
  else effectively shares this access.

**4. App settings — get the exact list**
- [ ] Ask for the full list of app setting *names* your code will read
  (you likely already know these from your own code — bring the list,
  don't make them guess)
- [ ] For each one, confirm: is it a Key Vault reference or a plain value?
- [ ] For any that are still placeholders (e.g. `REPLACE_ME`), confirm who
  fills in the real value — you, or them — and get that done in the
  meeting if possible, since a function with placeholder secrets will run
  but fail silently or error out.

**5. Deployment path — agree on one**
- [ ] Ask how code is expected to reach this Function App: a CI/CD
  pipeline they already have wired up, or manual deploy
  (`func azure functionapp publish` / zip deploy) from your machine. If a
  pipeline exists, get its name/location so you're not duplicating it by
  deploying manually on top.

**6. Before you leave the meeting**
- [ ] You can name the exact Function App, storage account, and Key Vault
  (not "one of the several in the resource group" — the *specific* ones)
- [ ] You know what role you currently have vs. what you still need, and
  who (if not them directly) needs to action any remaining grant
- [ ] You have a way to test end-to-end afterward without needing them
  again (e.g., you can trigger the function and check the output
  container yourself)

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

For secrets specifically (API keys, client secrets, connection strings),
see "Handling secrets: with and without Key Vault" below for the two
patterns and how to choose between them.

- **Permission needed:** Contributor on the Function App.

## Handling secrets: with and without Key Vault

Any setting that's sensitive (a client secret, an account key, a
connection string) can be stored two ways. Both work — the difference is
where the secret value actually lives and who can see it.

### Without Key Vault — plain app setting

You put the literal secret value directly into the Function App's
Application setting:

```
SHAREPOINT_CLIENT_SECRET = <the actual secret value>
```

- **Pros:** Simplest possible setup — no extra resource, no extra role
  grant, works the moment you save it.
- **Cons:** The raw value is visible to anyone with read access to the
  Function App's Configuration blade (which is a lower bar than Key Vault
  access — e.g. Contributor on the Function App is enough, no separate
  Key Vault role needed). It also shows up in plaintext if the app
  settings are ever exported (ARM template export, `az functionapp config
  appsettings list`, etc.).
- **When this is fine:** local development (`local.settings.json`, which
  is gitignored and never leaves your machine), throwaway test
  environments, or low-sensitivity values that aren't really secrets in
  practice.
- **When to avoid it:** production secrets, anything subject to
  compliance/audit requirements, or any value you'd be unhappy to see
  show up in a screenshot, log export, or ARM template dump.

### With Key Vault — secret reference

1. Store the actual secret as a **Secret** object inside a Key Vault
   resource (Key Vault → Objects → Secrets → Generate/Import).
2. In the Function App's Application setting, instead of the real value,
   put a pointer to it:
   ```
   SHAREPOINT_CLIENT_SECRET = @Microsoft.KeyVault(SecretUri=https://<vault-name>.vault.azure.net/secrets/<secret-name>)
   ```
3. Azure resolves that reference automatically at runtime, using the
   Function App's managed identity (Step 4) to fetch the real value from
   Key Vault, and injects the **resolved** value as the environment
   variable your code reads — your code itself never knows or cares that
   Key Vault was involved.

- **Pros:** The secret's actual value lives in exactly one place (Key
  Vault), with its own access policy, audit log (every read is logged),
  and rotation support — independent of who has access to the Function
  App's configuration. Revoking access is also one change in one place,
  rather than hunting down every setting that has the value pasted in.
- **Cons:** One more resource to manage, and the managed identity needs
  the **Key Vault Secrets User** role granted (Step 5) before the
  reference will resolve — until then, the setting shows an error instead
  of a value.
- **When to use it:** anything you'd call a real secret in a real
  environment — this project's `SHAREPOINT_CLIENT_SECRET` is a good
  example, since it's a credential that grants access to a SharePoint
  site.

### Choosing between them

A reasonable rule of thumb: **local development → plain value in
`local.settings.json`** (already excluded from git and never deployed to
Azure); **anything deployed to a real Azure environment → Key Vault
reference**, even for a personal dev/test resource group, since it costs
nothing extra to set up once the Key Vault already exists and avoids ever
having to "remember" to lock something down later. This project follows
exactly that split — see `RUNBOOK.md` section 4.4 for how this project's
SharePoint secret specifically gets moved into Key Vault, and section 4.7
for how to view a value once it's stored there.

### Step 7 — Write and deploy your code

If a CI/CD pipeline already exists for this Function App, that's usually
the intended path — check before deploying manually on top of it. If
you're deploying from your own laptop instead (no pipeline, or just
testing a one-off change), here are the two ways to do it:

**Option A — Azure Functions Core Tools (recommended if installed)**
```bash
func azure functionapp publish <function-app-name> --python
```
Run from your project root with your virtual environment activated. This
builds the deployment package, installs dependencies, and uploads it in
one step. You'll know it worked when the terminal prints a success message
ending in `Functions in <function-app-name>:` followed by your function's
name.

**Option B — Zip deploy via Azure CLI (no Core Tools needed)**
```bash
zip -r ../app.zip . -x ".venv/*" ".git/*"   # Windows: use Compress-Archive instead
az functionapp deployment source config-zip \
  --resource-group <resource-group-name> \
  --name <function-app-name> \
  --src ../app.zip
```
You build the zip yourself; the CLI just uploads it. Useful if Core Tools
isn't installed and you don't want to install it for a one-off deploy.

Either way, the code reads the app settings from Step 6 to know what to
connect to and what to do — deploying code never changes those settings.

- **Permission needed:** Contributor on the Function App.

### What actually lands in the target container

Before testing, it helps to know exactly what to expect in the container
once a run succeeds — both this project's specific behavior and the
general blob concept of "metadata" that makes it possible.

**Blob metadata, conceptually:** every blob in Azure Storage can carry an
optional set of key/value string pairs alongside its content — separate
from the file's bytes, viewable in the Portal under the blob's
**Properties → Metadata** without downloading the file. It's meant for
small descriptive tags (a few dozen short strings), not large data.
Metadata is set at upload time via the `metadata=` argument on
`upload_blob(...)` — this project's `_download_and_upload()` helper
(`function_app.py:28-35`) does exactly that for every file it writes.

**For each file this pipeline uploads, three metadata keys are set**
(`function_app.py:346-380`):

| Metadata key | Where it comes from | Notes |
|---|---|---|
| `Modified` | The file's `lastModifiedDateTime` from SharePoint/Graph | Always set |
| `Prefix` | The SharePoint list item's `Prefix` lookup column, resolved to its human-readable display value | Set only if the lookup resolves; a warning is logged if it can't be found |
| `ContentType` | The SharePoint list item's `HODSContentType` column (a multi-value lookup, serialized as a JSON array string) | Set only if the field exists on the item |

A few behaviors worth knowing when you're checking the container by hand:
- **Blob names are sanitized**, not a 1:1 copy of the SharePoint file name
  — `_to_blob_name()` (`function_app.py:158-164`) strips characters outside
  `[0-9A-Za-z._-]`, collapses repeated underscores, and trims leading/
  trailing separators. So `My Report (Final).pdf` might land as
  `My_Report_Final.pdf` — don't be surprised if a name doesn't match
  exactly.
- **Metadata values are ASCII-only** — `_to_blob_metadata_value()`
  (`function_app.py:167-189`) strips any non-ASCII characters, since Azure
  Blob metadata values are required to be ASCII. Accented characters or
  non-Latin text in a SharePoint field will be silently dropped from the
  metadata value (the file content itself is unaffected — only the
  metadata tag).
- **Re-uploads overwrite, not duplicate** — `upload_blob(..., overwrite=True)`
  means if the same file is modified in SharePoint and picked up again on
  a later run, it replaces the existing blob (same name) rather than
  creating a second copy.
- **One extra control blob, not your data:** alongside your actual files,
  the container also holds a blob literally named `last-sync` —
  (`function_app.py:504-505`), a plain text timestamp the pipeline reads
  on its next run to know where it left off. It's not one of your ingested
  files; don't delete it unless you intend to force the next run to
  re-scan everything from the beginning.

To inspect any of this yourself: Storage account → Containers →
`ingest-output` → click a blob → **Properties** tab shows its metadata
key/value pairs directly in the Portal.

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

## Repeated delete and redeploy (iterating during development)

While developing, you'll often want to tear something down and rebuild it
— either just the code, or the whole app. These are very different in
cost and risk; know which one you actually need before running anything.

### If you just changed your code — don't delete anything

This is the common case and needs no deletion at all. Just re-run the same
deploy command from Step 7 again:
```bash
func azure functionapp publish <function-app-name> --python
```
Each run overwrites the previous deployment package in place. The
Function App, its settings, its managed identity, and its role grants are
all untouched — only the code changes. Do this as many times as you want;
there's no cleanup needed between runs.

### If you want to reset the Function App itself (settings, identity, etc.)

Sometimes you want a genuinely clean slate — e.g., app settings have
accumulated cruft, or you suspect the managed identity/role grants are in
a bad state and want to redo them from scratch.

```bash
az functionapp delete --name <function-app-name> --resource-group <rg>
```

**What this loses, that you'll have to redo:**
- All Application settings (Step 6) — gone, not recoverable.
- The managed identity (Step 4) — a new Function App gets a *new* identity
  with a different object ID, even if you reuse the same name.
- Every role grant made to that identity (Step 5) — those grants pointed
  at the old identity's object ID, which no longer exists. You must
  re-grant Key Vault Secrets User / Storage Blob Data roles to the new
  identity after recreating.

**What this does NOT touch:** the storage account, Key Vault, and App
Service plan all survive — only the Function App resource itself is
deleted. Recreate it (Step 3), then redo Steps 4-7.

### If you're redeploying via the Bicep template (this project's pattern)

Re-running the same deployment command is the normal way to apply
infrastructure changes — it's **idempotent**, not destructive:
```bash
az deployment group create \
  --resource-group <rg> \
  --template-file infra/main.bicep \
  --parameters prefix=hods owner="<your name>" deployerObjectId="$DEPLOYER_OID"
```
Azure compares the template against what already exists and only changes
what's different — it does **not** delete and recreate everything from
scratch, and existing app settings/role assignments defined in the
template are reapplied, not duplicated. This is the safe way to "redeploy
the app" repeatedly without manually deleting anything first.

**Only delete resources by hand first if** you specifically want to force
a clean recreate (e.g., you manually changed something out-of-band that
Bicep won't overwrite back to its template state on its own). Even then,
prefer deleting the *one* resource that's wrong rather than the whole
resource group.

### Gotchas specific to delete-and-recreate

- **Storage account names are globally unique across all of Azure**, not
  just your subscription. If you delete one and immediately try to
  recreate it with the exact same name, you may briefly hit a "name
  already in use" error while Azure finishes releasing it — wait a few
  minutes and retry, or use a different name.
- **Key Vaults have soft-delete on by default** (typically a 90-day
  retention). Deleting a Key Vault doesn't free up its name immediately —
  recreating with the same name will fail with a conflict unless you
  either recover the soft-deleted vault (`az keyvault recover`) or purge it
  first (`az keyvault purge` — only if you're certain you don't need
  anything in it, and only if purge protection isn't enabled, in which
  case purging isn't even possible until the retention period passes).
- **Never delete the whole resource group as a shortcut** unless you
  intend to lose *everything* in it, including resources unrelated to this
  app that might be sharing the group (check the resource list first —
  see Appendix D in `RUNBOOK.md` for a guided tour of what's likely in
  there).

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

## Troubleshooting

General failure patterns for any Function App, independent of this
project's specific symptoms (for those, see `RUNBOOK.md`'s
[Troubleshooting](RUNBOOK.md#troubleshooting) table instead).

| Symptom | Likely cause | Fix |
|---|---|---|
| Function never runs, no error anywhere | Timer trigger's cron expression is wrong, or the Function App is stopped | Check **Overview** → is it "Running"? Check the trigger's schedule in `function_app.py` against actual time |
| Code deploys successfully but old behavior still happens | Deployment succeeded but Azure is still serving from a cached/previous package, or you deployed to the wrong Function App | Restart the Function App after deploying; double-check the `<function-app-name>` you targeted matches the one you opened in the Portal |
| `Authorization failed` / `Forbidden` errors in logs, but app settings look correct | Managed identity exists but was never granted a role (Step 5) | Check the Key Vault's or storage account's **Access control (IAM)** → look for the Function App's name in role assignments. If missing, it needs Key Vault Secrets User / Storage Blob Data role |
| A setting shows as a literal `@Microsoft.KeyVault(...)` string instead of resolving | The Key Vault reference's URI is wrong, the secret doesn't exist, or the identity lacks Key Vault Secrets User | Open the setting in the Portal — it'll show a status icon (resolved/error) you can hover for the exact reason |
| App settings table is empty / greyed out with a permissions banner | You only have Reader (or similar) on the Function App | You need Contributor — ask whoever manages your role assignments |
| Function runs but throws `KeyError` / `None` errors reading a setting | The setting name in `function_app.py` doesn't exactly match the name in Application settings (case-sensitive) | Compare the exact string in `os.getenv("...")` against the Portal's Configuration list |
| Works locally, fails only in Azure | A value in `local.settings.json` was never replicated as an Application setting in Azure — `local.settings.json` is local-only and is **never** read in the cloud, even if accidentally deployed | Set the same key/value (or Key Vault reference) under the Function App's Configuration in the Portal |
| Cold start takes 10-30+ seconds before first run | Normal behavior on a Consumption plan after idle time | Expected; switch to Premium plan if this is a problem (e.g. for HTTP-triggered functions with strict latency needs — usually irrelevant for a timer job) |
| Function App deploys but `Functions` blade shows zero functions | `function_app.py` has a syntax error, or `host.json`/`requirements.txt` issues prevented the runtime from indexing it | Check **Deployment Center** → **Logs**, or `Application Insights` → `traces` for a startup/indexing error |

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
