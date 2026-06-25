# Ingest Function — Run Book (Beginner's Guide)

This guide assumes you've never used Azure, Azure Functions, or any of the
tools mentioned here before. Every command is meant to be copied and pasted
exactly as written. If something doesn't work, check the
[Troubleshooting](#troubleshooting) table near the bottom before asking for
help.

## 1. What this is

This is a small program (an "Azure Function") that wakes up once an hour,
looks in a SharePoint folder for files that are new or have changed, and
copies those files into a cloud storage location (Azure Blob Storage). A
separate piece (Azure AI Search, not covered in this guide) then reads from
that storage and makes the files searchable.

This guide only covers getting files from SharePoint into Blob Storage —
not the search part.

## 2. Two ways to test this

There are two different ways to try this program out, and you should do
them **in this order**:

1. **Desktop testing** — run everything on your own laptop. This is the
   fastest way to check your changes work, and mistakes here don't affect
   anything real. You'll do this first.
2. **Cloud testing** — run the program for real inside Azure, the same way
   it will run in production. This is slower to set up but proves the
   whole thing actually works end-to-end. You'll do this after desktop
   testing succeeds.

Within desktop testing there are two options:
- **Option A**: use a fake, local stand-in for cloud storage (recommended
  first — nothing leaves your laptop except the SharePoint connection).
- **Option B**: use real Azure cloud storage, but still run the program on
  your laptop.

## 3. Words you'll see in this guide

A few terms come up a lot. Here's what they mean, in plain language:

| Term | What it means |
|---|---|
| **SharePoint** | Microsoft's file-storage/collaboration product. This program reads files from a SharePoint document library (basically a folder). |
| **Microsoft Graph** | The web API (a way for programs to talk to each other over the internet) that Microsoft provides for reading SharePoint data. This program uses it to list and download files. |
| **Entra ID** | Microsoft's identity/login system (it used to be called "Azure Active Directory"). It's where app credentials are created and managed. |
| **Service principal (SPN) / App registration** | A set of credentials (like a username and password, but for a program instead of a person) that lets this program log in to Microsoft Graph without a human typing a password. |
| **Tenant ID, Client ID, Client secret** | Three pieces of the service principal's credentials. Think of Tenant ID as "which company," Client ID as "which app," and Client secret as "the app's password." |
| **Azure Function** | A small piece of code that Azure runs for you, without you having to manage a server. This whole program is one Azure Function. |
| **Timer trigger** | The thing that makes the function "wake up" automatically on a schedule (by default, once an hour). |
| **Azure Blob Storage** | Microsoft's cloud file storage. A "container" is like a folder inside it, and a "blob" is a single file stored there. |
| **Azurite** | A small program that pretends to be Azure Blob Storage, running entirely on your laptop. Lets you test without touching real cloud storage or paying for it. |
| **Connection string** | A single long piece of text that contains everything needed to connect to a storage account (like an address + password combined). |
| **Terminal / command line** | A text-based window where you type commands instead of clicking buttons. On Mac it's called "Terminal," on Windows it's "PowerShell" or "Command Prompt." |
| **Virtual environment (venv)** | An isolated, private copy of Python just for this project, so installing things here doesn't affect any other Python project on your computer. |
| **The Azure Portal** | The website (portal.azure.com) where you click around to view and manage your Azure resources. |
| **Resource group** | A folder that groups all the Azure resources for one project together (the storage account, Key Vault, Function App, etc.), so you can find/manage/delete them as a set. |
| **Key Vault** | Azure's locked-safe service for secrets. Instead of a password sitting in plain text somewhere, it sits in Key Vault, and other resources are individually given permission to read it. |
| **Managed identity** | An automatic, built-in "login" that Azure gives to a resource like a Function App, so it can prove who it is to other Azure resources (like Key Vault) without anyone typing a password. |
| **Role assignment (RBAC)** | A specific permission grant — "this identity is allowed to do this action on this resource." For example, the Function App's managed identity is given a role assignment that says "you may read secrets from this Key Vault." No role assignment means no access, even if everything else looks configured correctly. |
| **Configuration drift** | When the real, live setup in Azure no longer matches what the infrastructure template (`infra/main.bicep`) says it should be — usually because someone changed a setting by hand in the Portal after the template was deployed. |

## 4. Security — please read before going further

This section is longer than you might expect for a "getting started" guide.
That's deliberate: the single biggest risk in a project like this isn't a
bug in the code, it's a secret (a password-like value) ending up somewhere
it shouldn't — a chat message, a public repo, a screenshot. Read this
before you create any credentials.

### 4.1 What's a "secret" here, and which ones actually need protecting

Three of the five SharePoint values from step 5.4 are secrets in different
senses:

| Value | How sensitive? | Why |
|---|---|---|
| `SHAREPOINT_CLIENT_SECRET` | **High — treat like a password** | Anyone who has it can log in as this app and read everything it has permission to read in SharePoint, for as long as the secret is valid. |
| `SHAREPOINT_TENANT_ID`, `SHAREPOINT_CLIENT_ID` | Low | These just identify "which company" and "which app" — like a username, not a password. Seeing them alone doesn't let anyone log in. Still avoid posting them publicly for no reason, but a coworker seeing them in a screenshot isn't an incident. |
| `BLOB_STORAGE_CONNECTION_STRING` (real Azure storage, not the local fake one) | **High — treat like a password** | Anyone who has it can read and write every file in that storage account. |

### 4.2 Local secrets (desktop testing)

- The file `local.settings.json` (you'll create it in the next section)
  holds real secrets — your SharePoint app's client secret, and possibly a
  real storage connection string. This file is set up to never be saved
  into the project's shared history (git). **Never** remove it from the
  `.gitignore` file, never paste its contents into any other file in the
  project, and never share it over chat, email, or a screenshot.
- If a client secret was ever shared over chat, email, or a screenshot,
  treat it as already compromised. Ask whoever manages Entra ID to create
  a new one for you (App registrations → your app → Certificates & secrets)
  before relying on the old one for anything beyond a quick, throwaway test.

### 4.3 Cloud secrets — what this project already protects, and what it doesn't

In the cloud (production) deployment, Azure has a dedicated service for
storing secrets safely called **Key Vault** — think of it as a locked safe
that other Azure resources can be given permission to read from, instead of
secrets sitting in plain text everywhere. This project deploys one
(`infra/main.bicep`).

Here's the part that's easy to miss: **not every secret in this project
actually uses it.**

- `AzureWebJobsStorage` and `BLOB_STORAGE_CONNECTION_STRING` (the storage
  connection string) **are** protected this way — in the Function App's
  settings, their value is `@Microsoft.KeyVault(SecretUri=...)`, a
  *reference* to the real value sitting safely in Key Vault, not the value
  itself (see `infra/main.bicep:244-245`). The Function App's identity is
  given permission to read from Key Vault (`infra/main.bicep:260-269`), but
  no human ever needs to see the real value in plain text.
- `SHAREPOINT_CLIENT_SECRET` **is not** — it's stored directly as a plain
  Function App setting (`infra/main.bicep:251`). After you fill it in
  (step 8.2), anyone with Reader access to the Function App in the Portal
  can click into **Settings → Environment variables** and read it in plain
  text. This is a known gap in the current setup, not something you did
  wrong — but you should know about it.

### 4.4 What to do about it — moving the SharePoint secret into Key Vault

If you have permission to create resources in the Azure subscription (or
can ask someone who does), it only takes a few minutes to close this gap:

1. In the [Azure Portal](https://portal.azure.com), find the Key Vault for
   this project — it's in the same resource group as the Function App,
   named something like `hods-kv-xxxxxxxx`. Click it.
2. In the left-hand menu, click **Objects → Secrets**, then
   **+ Generate/Import**.
3. **Name**: `sharepoint-client-secret`. **Value**: paste the real client
   secret from step 5.4. Click **Create**.
4. Click into the secret you just created, then click its current version.
   Copy the **Secret Identifier** — a long URI starting with `https://`.
5. Go to the Function App → **Settings → Environment variables**, find
   `SHAREPOINT_CLIENT_SECRET`, and replace its value with:
   `@Microsoft.KeyVault(SecretUri=<paste the URI from step 4 here>)`
6. Click **Apply** / **Save** and confirm. No extra permission setup is
   needed — the Function App's identity already has the **Key Vault
   Secrets User** role on the whole vault (it was granted that to read the
   storage secret), so it can read this new secret too.
7. Trigger a run (step 8.4) and check the logs (step 8.6) to confirm it
   still works.

There's no need to do this for `SHAREPOINT_TENANT_ID` or
`SHAREPOINT_CLIENT_ID` — per the sensitivity table above, they're
identifiers, not passwords.

### 4.5 If Key Vault genuinely isn't an option right now

Maybe you don't have permission to create Key Vault secrets, or this is a
short-lived test environment and it's not worth the setup. If so, reduce
the risk a different way instead of leaving it unaddressed:

- **Limit who can see the Function App's settings.** Ask whoever manages
  Azure access (RBAC) to make sure only people who actually need it have
  **Contributor** or **Reader** access to this Function App / resource
  group — not "everyone in the team."
- **Rotate the secret more often than you would a Key-Vault-protected
  one** (e.g. every 30-60 days instead of the default expiry) — go to
  Entra ID → App registrations → your app → Certificates & secrets → add a
  new one, update the Function App setting, then delete the old one.
- **Never copy the value anywhere else** — not into a chat message to ask
  "is this right?", not into a ticket, not into a doc. If you need help
  debugging, share the *error message*, not the secret.
- Treat moving it into Key Vault as a follow-up task, not something to
  forget about — it's a small amount of work for a real reduction in risk.

### 4.6 A few more habits worth building now

- **Least privilege on the Entra ID app.** When you register the app
  (step 5.4), only grant the Graph permission it actually needs
  (`Sites.Read.All`, or — better — a site-scoped permission if your Entra
  ID admin supports it). Don't grant broader permissions "just in case."
- **Never log secrets.** If you ever add a `logging.info(...)` or
  `print(...)` line while debugging, double check it doesn't include
  `client_secret`, a connection string, or a full request/response body
  that might contain one. The existing code is careful about this — keep
  it that way in anything you add.
- **HTTPS only, always.** The storage account and Function App in this
  project already enforce HTTPS-only traffic and a minimum TLS version
  (`infra/main.bicep:35-36`) — don't disable that if you're customizing
  the infrastructure.

### 4.7 Where to find secrets that already exist

You won't always be creating a secret from scratch — often someone else
set one up earlier, and you just need to find it. Here's where each one
lives:

| Secret | Where to find it |
|---|---|
| `SHAREPOINT_CLIENT_SECRET` (the SPN's client secret) | [Azure Portal](https://portal.azure.com) → **Entra ID** → **App registrations** → the app for this project → **Certificates & secrets**. **Important:** Azure never shows you an *existing* secret's value again after the moment it was created — you'll only see a masked value like `••••••••`. If you need the actual value and don't have it saved somewhere safe already, you can't "look it up" — you have to click **+ New client secret** to create a new one (and then delete the old one once you've updated the Function App setting, so there's only ever one valid secret at a time). |
| `BLOB_STORAGE_CONNECTION_STRING` (real Azure storage account) | The **Storage account** resource → **Security + networking → Access keys** → click **Show** next to either key's connection string, then the copy icon. Unlike the SharePoint secret, this one *can* be viewed again later by anyone with the right permission (see 4.8 below) — Azure just hides it by default so it doesn't show up accidentally on a screen-share. |
| Anything already stored in **Key Vault** (e.g. after following 4.4) | The **Key Vault** resource → **Objects → Secrets** → click the secret name → click its current version. There's a **Show secret value** button (you may need the **Key Vault Secrets User** role to use it — see 4.8). |
| A value already typed into a Function App setting (e.g. checking what's currently in `SHAREPOINT_TENANT_ID`) | The **Function App** → **Settings → Environment variables** → **App settings** tab. Values are masked with `•••` by default; click the **eye icon** at the right of the row, or check the **Show values** toggle near the top of the table, to reveal them. |

### 4.8 How to check whether you have permission to do something

If a button is greyed out, or you get a red error banner mentioning
"Forbidden," "AuthorizationFailed," or "does not have authorization,"
that's Azure telling you your account hasn't been granted the role
(see **Role assignment (RBAC)** in the glossary above) needed for that
action — it's not a bug, and clicking around more won't fix it.

**To see what you're currently allowed to do:**
1. Go to the resource (or resource group, or subscription) you're trying
   to act on in the Portal.
2. Left-hand menu → **Access control (IAM)**.
3. Click the **Check access** (or **View my access**) button near the
   top of the page. It lists every role you've been assigned on that
   resource — if the list is empty, you have no access to it at all.

**Roles you're likely to need for this project, and what each lets you do:**

| Role | Lets you... | Needed for |
|---|---|---|
| **Reader** | View resources and their (non-secret) settings, but not change anything | Browsing the Portal, following section 11's checks |
| **Contributor** | Create, edit, and delete most resources (Function Apps, storage accounts, etc.), but not manage who else has access | Deploying infra (8.7.1), changing Function App settings (8.2), redeploying code (8.3/8.7.2) |
| **Key Vault Secrets User** | Read (and with some Key Vault configurations, also list) secret *values* inside a specific Key Vault | Viewing/copying an existing secret from Key Vault (4.7), or completing the Key Vault migration in 4.4 |
| **Owner** | Everything Contributor can do, plus managing role assignments for other people | Granting *other* people access — you generally don't need this one yourself |

**If you don't have a role you need:** don't try to work around it (for
example, by asking someone to share their own login). Instead, ask
whoever manages access for your Azure subscription — often called the
subscription or resource group **Owner** — to grant you the specific role
from the table above, on the specific resource (or resource group) you
need it on. Tell them the exact role name and resource; "can you give me
access" is harder for them to action than "can you give me **Contributor**
on the `hods-rg` resource group."

For more on how Azure's permission scopes work, how to grant (not just
check) a role yourself, and how this differs from Entra ID and SharePoint
permissions, see [Appendix B](#appendix-b-azure-portal-orientation-and-rbac-deep-dive).

## 5. Before you start (one-time setup)

Do these steps once, in order. You can skip a step if you've already done
it.

### 5.1 Install Python 3.13

This project is written in Python, so you need Python installed.
Download it from [python.org](https://www.python.org/downloads/) if you
don't already have it. To check what you have, open a terminal and run:

```bash
python --version
```

You should see something starting with `Python 3.13`. If you see a much
older version, or "command not found," install Python 3.13 before
continuing.

### 5.2 Install Azure Functions Core Tools

This is the program that actually runs the Azure Function on your laptop.
You do **not** need Node.js or npm to get it — install it directly:

- **Windows:** run `winget install Microsoft.Azure.FunctionsCoreTools` in a
  terminal, or download the MSI installer from
  [the Core Tools releases page](https://github.com/Azure/azure-functions-core-tools/releases).
- **macOS:** `brew tap azure/functions && brew install azure-functions-core-tools@4`
- **Linux:** follow the apt instructions for your distro on
  [Microsoft's Core Tools install page](https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local) —
  it adds Microsoft's package repo, then `apt-get install azure-functions-core-tools-4`.

To check it worked, open a new terminal and run:

```bash
func --version
```

Any version number printed back means it's installed. (If you happen to
already have Node.js installed for some other reason, `npm install -g
azure-functions-core-tools@4` also works — but it's not required.)

### 5.3 Install a local storage emulator (Azurite) — optional, only needed for Option A testing

[Option A testing](#6-desktop-testing--option-a-fake-cloud-storage-do-this-first)
in section 6 below uses Azurite (see the glossary) so you can test without
touching real cloud storage. You can skip this step for now and come back
to it when you reach section 6.

The easiest way to get Azurite, since you already have VS Code installed,
is the **Azurite VS Code extension** — no Node.js required:

1. In VS Code, click the Extensions icon in the left sidebar (or press
   `Ctrl+Shift+X` / `Cmd+Shift+X`).
2. Search for **Azurite** (published by Microsoft) and click **Install**.

Section 6 will tell you how to start it from VS Code when you get there.
If you'd rather not use VS Code for this, two alternatives are covered in
section 6 as well: running Azurite via Docker, or via `npx` (which does
need Node.js).

### 5.4 Get your SharePoint credentials

You need a SharePoint site with a document library to copy files from, and
an Entra ID "app registration" (service principal) that's allowed to read
it via Microsoft Graph. If someone has already set these up for you, skip
to the bulleted list below. If you're setting this up from scratch (e.g. a
test tenant) and have Entra ID admin access, here's the full path:

**If you don't already have a SharePoint site to test against:**
1. Get a free Microsoft 365 developer tenant at
   [developer.microsoft.com/microsoft-365/dev-program](https://developer.microsoft.com/microsoft-365/dev-program)
   (skip this if you already have access to a Microsoft 365 tenant).
2. Go to `https://YOUR-TENANT.sharepoint.com`, click **+ Create site** →
   **Team site**, and give it a name (e.g. `HODS Documents`). Note the site
   URL — you'll need its hostname and path below.
3. Click **Documents** in the left navigation, then **+ Add column** to
   add any metadata columns you want copied to blob metadata later (this
   project supports copying one column via `SHAREPOINT_METADATA_COLUMN`).
4. Upload a few test files into the library.

**Register the Entra ID app (needs Entra ID admin rights):**
1. Go to [portal.azure.com](https://portal.azure.com) → search **App
   registrations** → **New registration**. Name it anything (e.g.
   `hods-ingest-app`), account type **Single tenant**, click **Register**.
2. Copy the **Application (client) ID** → this is your `Client ID`.
3. Copy the **Directory (tenant) ID** → this is your `Tenant ID`.
4. Click **Certificates & secrets** → **New client secret** → give it a
   description and expiry → **Add** → copy the **Value** immediately (it's
   only shown once) → this is your `Client secret`.
5. Click **API permissions** → **Add a permission** → **Microsoft Graph**
   → **Application permissions** → search for and add `Sites.Read.All`
   (or a more limited, site-specific permission).
6. Click **Grant admin consent for [your org]** → **Yes**.

Either way, you end up needing five pieces of information:

1. The **Tenant ID**
2. The **Client ID**
3. The **Client secret**
4. The SharePoint **site hostname** (for example `contoso.sharepoint.com` —
   no `https://` in front)
5. The SharePoint **site path** (for example `/sites/HODS`)

Keep these somewhere safe — you'll paste them into a file in a moment.

For more background on how a SharePoint site's URL maps to these values,
what a "drive"/library actually is, and SharePoint's own permission levels
(separate from the Entra ID app permission above), see
[Appendix A](#appendix-a-sharepoint-orientation-and-permissions).

### 5.5 Open a terminal and set up the project

Open a terminal (Terminal on Mac, PowerShell on Windows) and run these
commands one at a time. The `#` lines are just explanations — you don't
need to type them.

```bash
# Move into the project's ingest folder
cd poc-hods-ingest

# Create an isolated, private copy of Python just for this project
python -m venv .venv

# "Activate" that private copy so the next commands use it
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# Install all the Python packages this project needs
pip install -r requirements.txt

# Install pytest too, so you can run the automated tests later
pip install pytest
```

You'll need to run the `source .venv/bin/activate` (or
`.venv\Scripts\activate` on Windows) line again every time you open a new
terminal window for this project. You'll know it worked because your
terminal prompt will show `(.venv)` at the start of the line.

### 5.6 Create your settings file

This project reads its configuration (including secrets) from a file
called `local.settings.json`, which is just a plain text file. A template
already exists — copy it:

```bash
cp local.settings.json.example local.settings.json
```

Now open `local.settings.json` in any text editor and fill in the five
SharePoint values from step 5.4:

```json
"SHAREPOINT_TENANT_ID": "<paste your Tenant ID here>",
"SHAREPOINT_CLIENT_ID": "<paste your Client ID here>",
"SHAREPOINT_CLIENT_SECRET": "<paste your Client secret here>",
"SHAREPOINT_SITE_HOSTNAME": "<paste your site hostname here, e.g. contoso.sharepoint.com>",
"SHAREPOINT_SITE_PATH": "<paste your site path here, e.g. /sites/HODS>",
```

Also check `SHAREPOINT_LIBRARY_DRIVE_NAME` — it should match the exact
name of the document library (folder) in SharePoint you want to copy files
from. The default is `Documents`.

### 5.7 Sanity-check your setup with the automated tests

Before touching anything real, run the project's automated tests. These
don't need any real SharePoint or Azure connection — they just check that
the code itself works correctly:

```bash
pytest tests/ -v
```

You should see a list of test names each ending in `PASSED`, and a final
line like `25 passed`. If you see `FAILED` or an error instead, something
is wrong with your Python setup — see
[Troubleshooting](#troubleshooting) below.

## 6. Desktop testing — Option A: fake cloud storage (do this first)

This option uses Azurite (see the glossary) so you can test the whole
SharePoint-to-storage flow without touching any real Azure storage
account. You still need the real SharePoint credentials from step 5.4,
since there's no fake stand-in for SharePoint.

First, make sure these two lines in `local.settings.json` are left exactly
as they came in the template (they tell the program to use the fake
storage):

```json
"AzureWebJobsStorage": "UseDevelopmentStorage=true",
"BLOB_STORAGE_CONNECTION_STRING": "UseDevelopmentStorage=true",
```

You'll need the fake storage service (Azurite) and the actual function
running at the same time.

**Start Azurite** using whichever way you set it up in step 5.3:

- **VS Code extension (recommended):** open the Command Palette
  (`Ctrl+Shift+P` / `Cmd+Shift+P`), type **Azurite: Start**, and press
  Enter. It runs quietly in the background — look for "Azurite Blob
  Service is successfully listening" in the bottom status bar or the
  Output panel. No terminal window needed; skip ahead to starting the
  function below.
- **Docker (alternative):**
  ```bash
  docker run -p 10000:10000 -p 10001:10001 -p 10002:10002 mcr.microsoft.com/azure-storage/azurite
  ```
  Leave this terminal window open and running — closing it stops the fake
  storage service.
- **npx (alternative, needs Node.js):**
  ```bash
  # macOS/Linux
  npx -y azurite --silent --location .azurite
  ```
  ```powershell
  # Windows PowerShell
  if (!(Test-Path .azurite)) { New-Item -ItemType Directory .azurite | Out-Null }; $env:NODE_OPTIONS=''; npx -y azurite --location .azurite --silent
  ```
  Leave this terminal window open and running — closing it stops the fake
  storage service.

If you used Docker or npx, you'll now need **a second terminal window**
for the function itself (the VS Code extension path doesn't use up a
terminal, so you can use your first one).

**In a second terminal window**, move into the project folder, activate
your virtual environment again (step 5.5), and start the function:

```bash
func start
```

If you see an error saying port 7071 is already being used by something
else, run this instead:

```bash
func start --port 7072
```

You should see log output ending with something like `Functions:` followed
by `Ingest`. Leave this running too.

**Trigger the function immediately** instead of waiting up to an hour for
the timer. Open a **third terminal window** and run:

```bash
curl -X POST http://localhost:7071/admin/functions/Ingest \
  -H "Content-Type: application/json" \
  -d "{}"
```

This sends a request that tells the already-running function "run right
now." Watch the second terminal window (where `func start` is running) —
you should see log lines about connecting to SharePoint and uploading
files.

**Check the result.** Download and install
[Azure Storage Explorer](https://azure.microsoft.com/en-us/products/storage/storage-explorer)
(a free app from Microsoft for browsing storage accounts, including the
fake local one). When you open it, it should already show a connection
to "Local & Attached" / the Azurite emulator without you needing to type
anything — its default settings are pre-configured to match Azurite.

What success looks like:
- Inside the emulator, find a container named `ingest-output`.
- Inside that container, you should see one file for each file SharePoint
  had that was new or changed, plus one extra file named `last-sync`.
- `last-sync` is a small text file containing a timestamp — it's how the
  program remembers what it already copied, so it doesn't re-copy the same
  files every time it runs.

## 7. Desktop testing — Option B: real Azure cloud storage

Use this once Option A works, to test against a real storage account
instead of the fake one — while still running the function on your
laptop.

First you need a connection string for a real storage account. In the
[Azure Portal](https://portal.azure.com):

1. In the search bar at the top, type the name of your storage account
   (ask whoever set up the Azure resources if you don't know it) and click
   it.
2. In the left-hand menu, click **Access keys**.
3. Click **Show** next to "Connection string," then click the copy icon.

> **Warning:** This connection string is a secret — anyone who has it can
> read and write to your storage account. Paste it **only** into
> `local.settings.json` on your own laptop. Never paste it into any other
> file, chat message, or commit it to git.

Paste it into `local.settings.json`:

```json
"BLOB_STORAGE_CONNECTION_STRING": "DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net",
"BLOB_CONTAINER_NAME": "ingest-output"
```

You can leave `AzureWebJobsStorage` as `UseDevelopmentStorage=true` — it's
only used by Azure Functions for its own internal bookkeeping, not for the
actual file copying, so it can keep using the fake emulator. (You'll still
need Azurite running from Option A for that reason — or set
`AzureWebJobsStorage` to the same real connection string too, if you'd
rather not run Azurite at all.)

Start the function and trigger it the same way as Option A:

```bash
func start
```

```bash
curl -X POST http://localhost:7071/admin/functions/Ingest -H "Content-Type: application/json" -d "{}"
```

**Check the result** — this time in the real Azure Portal instead of
Storage Explorer:

1. Go to your storage account in the Portal.
2. Click **Containers** in the left-hand menu, then click `ingest-output`.
3. Confirm you see the uploaded files, and a `last-sync` file.
4. Click on one of the uploaded files, then look for "Metadata" — you
   should see values for `Modified`, `Prefix`, and `ContentType`.

## 8. Cloud testing — running the real thing in Azure

This is the final step: running the function the way it actually runs in
production, fully inside Azure, with nothing on your laptop. Do this after
both desktop options above have worked.

This assumes someone has already created the Azure resources for this
project (the storage account, the Function App itself, etc.) using the
infrastructure template in `infra/main.bicep`. You're not creating
anything new here — just configuring and deploying code to what already
exists. **If nobody has deployed the infrastructure yet and there's no
CI/CD pipeline set up to do it, see [8.7](#87-manual-deployment--what-to-do-if-theres-no-cicd-pipeline-yet)
below first, then come back here.**

### 8.1 Find your Function App in the Portal

1. Go to [portal.azure.com](https://portal.azure.com).
2. In the search bar, search for the resource group for this project (ask
   whoever deployed the infrastructure for its name if you don't know it).
3. Inside the resource group, you'll see a list of resources with
   different **Type** values (Function App, App Service, Storage account,
   Key Vault, etc.). Look for one whose **Type** column says "Function
   App" — that's the one you want. As a naming hint, it often contains
   `func` rather than `webapp` in its name (for example
   `az-func-hodsai-dev-cae-001`, **not** `az-webapp-hodsai-dev-cae-001` —
   the latter is a different resource, a plain web app, not this
   project's ingest function). Click the Function App one.

**How to confirm you clicked the right one** (the resource group list is
easy to misread, so double-check once the resource page opens):
- Near the top of the page, next to the resource's name, it should
  explicitly say **"Function App"**.
- In the left-hand menu, you should see **Functions** and **Durable
  Functions** entries. These two only ever appear on Function Apps — a
  regular Web App's left menu doesn't have them.

If the page you're on doesn't show those two menu items (for example, it
shows "Deployment Center" instead, with no "Functions" entry), you're on
the wrong resource — go back to the resource group list and look again.
Adding settings to the wrong resource won't do anything useful, since the
ingest code isn't running there.

### 8.2 Fill in the SharePoint settings

When the infrastructure was first created, the SharePoint credential
fields were filled in with placeholder text (`REPLACE_ME`) since they're
secrets and can't be safely stored in the infrastructure template. You
need to replace them with real values:

1. On the Function App's page, in the left-hand menu, click **Settings**,
   then **Environment variables** (it may also be labeled "Configuration"
   / "App settings" depending on your Portal version).
2. Find each of these and click into its value to edit it:
   - `SHAREPOINT_TENANT_ID`
   - `SHAREPOINT_CLIENT_ID`
   - `SHAREPOINT_CLIENT_SECRET`
   - `SHAREPOINT_SITE_HOSTNAME`
   - `SHAREPOINT_SITE_PATH`
3. Replace each `REPLACE_ME` placeholder with the matching real value from
   step 5.4.
4. Click **Apply** / **Save** (the exact button label depends on your
   Portal version) and confirm when prompted.

You should not need to touch `BLOB_STORAGE_CONNECTION_STRING` or
`AzureWebJobsStorage` — those are already wired up to read from a secure
Key Vault automatically.

### 8.3 Deploy the code

This step uploads your actual program code to the Function App. From your
terminal, with your virtual environment activated (step 5.5):

```bash
func azure functionapp publish <function-app-name> --python
```

Replace `<function-app-name>` with the exact name you saw in step 8.1.
This step talks to Azure over the internet and can take a minute or two.
You'll know it worked when the terminal prints a success message ending
with something like `Functions in <function-app-name>:` followed by
`Ingest`.

### 8.4 Trigger a run

Unlike desktop testing, you can't use `curl` against a cloud function the
same simple way (it requires extra authentication). Instead, trigger it
from the Portal:

1. On the Function App's page, click **Functions** in the left-hand menu.
2. Click **Ingest**.
3. Click **Code + Test**, then click **Test/Run** near the top.
4. Leave the input body empty and click **Run**.

### 8.5 Check the result

Same idea as desktop testing, but everything's in the Portal now:

1. Go to the storage account used by this Function App (find it the same
   way as step 7's instructions, but the account name will be the real
   one this Function App is configured to use).
2. Click **Containers** → `ingest-output`.
3. Confirm uploaded files and a `last-sync` file are present.

### 8.6 Check the logs

If something doesn't look right, check the application logs:

1. On the Function App's page, find **Application Insights** in the left
   menu (or open the separate Application Insights resource in the same
   resource group).
2. Click **Logs**.
3. Paste this query in and click **Run**:
   ```kusto
   traces | where severityLevel >= 3 and message has "Failed to sync"
   ```
4. If any rows show up, that's an error from the run — read the message
   text for details, and check the [Troubleshooting](#troubleshooting)
   table below.

For a more thorough validation pass once you've gotten one successful run
(checking permissions, large-file behavior, and more), see
`E2E-CHECKLIST.md` in this same folder.

### 8.7 Manual deployment — what to do if there's no CI/CD pipeline yet

`azure-pipelines/ingest.yml` and `azure-pipelines/infra.yml` automate
deployment through Azure DevOps (see `azure-pipelines/README.md` for how
to set that up). If those pipelines aren't configured yet, or you just
want to push out a one-off change without waiting for a pipeline run, you
can do everything they do by hand from your own terminal. There are two
separate things to deploy: the infrastructure (rarely) and the function
code (every time you change `function_app.py`).

#### 8.7.1 Deploying the infrastructure by hand (only needed once, or when `infra/main.bicep` changes)

Skip this if someone already deployed the infrastructure — i.e. you found
a resource group with a Function App already in it back in step 8.1.

1. Make sure the Azure CLI is logged in and pointed at the right
   subscription:
   ```bash
   az login
   az account set --subscription "<your subscription name or ID>"
   ```
2. Create a resource group to hold everything, if one doesn't already
   exist (pick any name and region):
   ```bash
   az group create --name hods-rg --location eastus
   ```
3. Deploy the Bicep template into it. This single command creates the
   storage account, Key Vault, Function App, and everything else
   `infra/main.bicep` defines — it's the same command
   `azure-pipelines/infra.yml`'s Deploy stage runs:
   ```bash
   DEPLOYER_OID=$(az ad signed-in-user show --query id -o tsv)

   az deployment group create \
     --resource-group hods-rg \
     --template-file infra/main.bicep \
     --parameters prefix=hods \
                  owner="<your name>" \
                  deployerObjectId="$DEPLOYER_OID"
   ```
   - `prefix` controls the name of every resource it creates (e.g.
     `hods-ingest-xxxxxxxx` for the Function App) — keep it short,
     lowercase letters only.
   - This takes a few minutes. When it finishes, it prints the names of
     everything it created — note the Function App's name for step 8.1.
4. Continue from [step 8.1](#81-find-your-function-app-in-the-portal)
   above using the resource group you just created.

> This template deploys more than just the ingest function — it's the
> original full-stack template this standalone repo was trimmed from (see
> `README.md`). The extra resources it creates (AI Search, Azure OpenAI,
> API/UI hosting) aren't used by anything in this guide; you can ignore
> them or delete them later if you don't need them.

#### 8.7.2 Deploying the function code by hand (every time you change `function_app.py`)

This is exactly what [step 8.3](#83-deploy-the-code) above already does —
`func azure functionapp publish` *is* the manual deployment path, no
CI/CD pipeline required. Run it any time after making a code change.

If you don't have the Azure Functions Core Tools installed (step 5.2) and
don't want to install them just for a one-off deploy, you can zip and
deploy with the Azure CLI alone instead:

```bash
cd poc-hods-ingest
zip -r ../ingest.zip . -x ".venv/*" ".azurite/*"   # Windows: use Compress-Archive instead of zip

az functionapp deployment source config-zip \
  --resource-group hods-rg \
  --name <function-app-name> \
  --src ../ingest.zip
```

Either method achieves the same result as the Deploy stage in
`azure-pipelines/ingest.yml`.

## 9. Settings you might want to change

These control how often the function runs and how much it does per run.
In desktop testing, change them in `local.settings.json`. In cloud
testing, change them in the Portal the same way you edited the SharePoint
settings in step 8.2.

| Setting | Default | What it does |
|---|---|---|
| `INGEST_SCHEDULE_CRON` | `0 0 * * * *` (once an hour) | How often the timer fires. You can shorten this for a quick local test (e.g. every 2 minutes), but never use a short schedule against real SharePoint/production data — Microsoft Graph will start rejecting requests (see the `429` row in Troubleshooting) if you call it too often. |
| `INGEST_MAX_FILES_PER_RUN` | `500` | The most files copied in one run. Lower this (e.g. to `10`) to do a quick test against a SharePoint library with lots of files, without waiting for everything to copy. |

## 10. Optional: running the deeper automated test

There's one more automated test beyond the basic ones from step 5.7. It's
optional, but it gives you extra confidence that re-running the program
multiple times in a row doesn't re-copy files it already copied
(this is called "idempotency" — doing something twice has the same result
as doing it once).

This test starts its own temporary, throwaway copy of Azurite (separate
from any Azurite you might already have running from section 6), runs the
copy logic three times in a row using made-up SharePoint data, and checks
that only the first run actually uploads anything. Unlike section 6,
this specific test always needs Node.js — it starts its temporary Azurite
copy via `npx` directly in the test code, regardless of which Azurite
option (VS Code extension, Docker, or npx) you used for manual testing
earlier:

```bash
pytest tests/test_ingest_azurite_integration.py -v
```

If Node.js or `npx` isn't available, this test automatically skips itself
instead of failing — so it's safe to ignore if it doesn't run.

## 11. Someone else already set up the cloud infrastructure — how do you know it's actually correct?

It's common to inherit an Azure setup someone (or some automated pipeline)
already built, instead of building it yourself. "It's deployed" and "it's
deployed *correctly*" are different things — a setup can look complete in
the Portal while still being broken in a way that only shows up when the
function actually tries to run. This section is a checklist for telling
the difference, entirely by clicking around the Portal — no command line
required (an optional command-line shortcut is included for each check, in
case you're comfortable with one).

Do these checks **in order** — each one tends to explain a failure in the
next one, so working top to bottom is faster than jumping around.

### 11.1 Do all the pieces exist?

1. Go to [portal.azure.com](https://portal.azure.com) → **Resource
   groups** → open the resource group for this project.
2. You should see, among other things: a **Storage account**, a **Key
   vault**, a **Function App**, an **Application Insights** resource, and
   a **Log Analytics workspace**. If any of these are missing, the
   deployment was incomplete — go back to
   [8.7.1](#871-deploying-the-infrastructure-by-hand-only-needed-once-or-when-infrastructure-main-bicep-changes)
   and re-run the deployment.

### 11.2 Is the Function App actually running?

1. Click the **Function App** resource.
2. On its **Overview** page, check the **Status** field — it should say
   `Running`. `Stopped` means nobody (or some cost-saving policy) turned
   it off.
3. In the left-hand menu, click **Functions**. You should see one named
   `Ingest` in the list. If the list is empty, the infrastructure exists
   but the code was never deployed — do
   [step 8.3](#83-deploy-the-code).

### 11.3 Are the SharePoint settings actually filled in?

1. On the Function App's page, click **Settings → Environment variables**.
2. Look through the list for any value still showing `REPLACE_ME` — that's
   placeholder text left over from the infrastructure template, meaning
   nobody completed [step 8.2](#82-fill-in-the-sharepoint-settings) yet.
   If you see it, fill those values in now before continuing.

### 11.4 Is the Key Vault connection actually working (not just configured)?

This is the check most people skip, and the most common way a setup
*looks* fine but silently fails. Recall from
[section 4.3](#43-cloud-secrets--what-this-project-already-protects-and-what-it-doesnt)
that `AzureWebJobsStorage` and `BLOB_STORAGE_CONNECTION_STRING` aren't
plain values — they're references that point at a secret stored in Key
Vault. A reference can be typed correctly and still fail to resolve if the
Function App was never given permission to read that Key Vault.

1. Still on **Environment variables**, find `BLOB_STORAGE_CONNECTION_STRING`.
   Its **Source** column should say **Key Vault Reference**, and there
   should be a small status icon next to it — hover over it.
2. A green checkmark / "Resolved" means it's actually working. A red
   warning icon / "Error" means the Function App can't read the secret —
   almost always because of a missing permission grant, which you'll
   check next.
3. Click the **Key vault** resource (from section 11.1) → **Access
   control (IAM)** → **Role assignments** tab.
4. Look for a row where the **Role** is **Key Vault Secrets User** and
   the assigned identity is the Function App itself (it'll be listed by
   the Function App's name). If that row is missing, that's the bug —
   the Function App's managed identity was never granted permission to
   read from this vault, and every cold start will fail to start up
   properly even though the app setting "looks" correct.

> **Optional command-line shortcut**, if you have the Azure CLI installed
> and logged in (`az login`):
> ```bash
> az rest --method get --uri "https://management.azure.com$(az functionapp show --name <function-app-name> --resource-group <rg> --query id -o tsv)/config/configreferences/appsettings?api-version=2022-03-01"
> ```
> Look for `"status": "Resolved"` next to each setting in the output.

### 11.5 Does the storage account look locked down?

1. Click the **Storage account** resource.
2. Click **Settings → Configuration**. Confirm **Secure transfer
   required** is **Enabled** and **Minimum TLS version** is **Version
   1.2**. These should already be set this way by the infrastructure
   template — if they're not, someone changed them by hand after
   deployment.
3. Click **Data storage → Containers** → `ingest-output`. Confirm
   **Public access level** is **Private** — files in this container
   should never be reachable by a plain public URL.

### 11.6 Are logs actually flowing?

A Function App can be "Running" with everything wired up and still have
no working logging — which means when something does go wrong later,
you'll have no way to see why.

1. On the Function App's page, click **Application Insights** in the
   left menu (or find the separate Application Insights resource from
   section 11.1 directly).
2. Click **Logs**, paste in `traces | take 10`, and click **Run**.
3. If you see rows come back (even just routine startup messages), logs
   are flowing correctly. If the query returns nothing at all — even
   after the app has had time to run — something's disconnected between
   the Function App and Application Insights, and you should fix that
   before relying on logs to debug anything else.

### 11.7 Optional, more advanced: has anyone changed things by hand since deployment?

This check is for configuration drift (see the glossary in section 3) —
it's useful but not required to confirm the basics above. It needs the
Azure CLI:

```bash
az deployment group what-if \
  --resource-group <resource-group> \
  --template-file infra/main.bicep \
  --parameters prefix=hods owner=<your-name> deployerObjectId=$(az ad signed-in-user show --query id -o tsv)
```

This compares what the infrastructure template says should exist against
what's actually there, without changing anything. If it reports no
differences, the live setup matches the template exactly. If it lists
changes, someone modified something in the Portal directly after the
template was last deployed — worth knowing about, since the next template
deployment would undo that manual change.

### 11.8 The real proof: does it actually move files?

Everything above confirms the *plumbing* is connected correctly — none of
it proves the SharePoint-to-Blob logic actually works. For that, do
[steps 8.4 through 8.6](#84-trigger-a-run) (trigger a run, check the
storage container, check the logs for errors), then work through the
fuller functional checklist in `E2E-CHECKLIST.md` in this same folder.

## Troubleshooting

A quick note before the table: `401`, `403`, and `429` are HTTP status
codes — short numeric codes a web service sends back to say what went
wrong. `401` means "you're not logged in correctly," `403` means "you're
logged in, but not allowed to do that," and `429` means "you're asking too
often, slow down."

| Symptom | Likely cause | Fix |
|---|---|---|
| Graph token request fails with `401` | Your Tenant ID, Client ID, or Client secret (step 5.4) is wrong, or the secret has expired | Double check `SHAREPOINT_TENANT_ID` / `SHAREPOINT_CLIENT_ID` / `SHAREPOINT_CLIENT_SECRET`; ask whoever manages Entra ID to generate a new secret if it's expired |
| Login succeeds but looking up the SharePoint site/library fails with `403` | The Entra ID app registration is missing the `Sites.Read.All` permission, or nobody clicked "Grant admin consent" for it | Ask whoever manages Entra ID to go to App registrations → the app → API permissions, add the permission, and click "Grant admin consent" |
| Error message `Unable to resolve SharePoint site id` | `SHAREPOINT_SITE_HOSTNAME` or `SHAREPOINT_SITE_PATH` (step 5.4) is typed wrong | Double-check the exact hostname (no `https://` in front) and path (starts with `/sites/...`) by looking at your SharePoint site's URL in a browser |
| Error message `Drive 'X' not found in site` | `SHAREPOINT_LIBRARY_DRIVE_NAME` doesn't match an actual document library's name | Open the SharePoint site in a browser and check the exact library name (the match ignores upper/lowercase, but the name itself must match) |
| Lots of `429` errors | The timer schedule is too tight, or too many files are being processed per run | Use the default once-an-hour schedule; lower `INGEST_MAX_FILES_PER_RUN` |
| File upload fails with `AuthorizationFailure` or `AuthenticationFailed` | The storage connection string is wrong/expired, or the storage account is blocking your network/IP address | Re-copy the connection string from the Portal (step 7); check the storage account's "Networking" settings if you're on a restricted network |
| `func start` says port 7071 is already in use | Some other program on your laptop is already using that port | Run `func start --port 7072` instead |
| `ModuleNotFoundError` when running `func start` or `pytest` | Your virtual environment isn't activated, or you skipped `pip install -r requirements.txt` | Re-run the `source .venv/bin/activate` (or Windows equivalent) command from step 5.5, then re-run `pip install -r requirements.txt` |
| The function runs but uploads 0 files, even though you know SharePoint has new files | The `last-sync` file already has a timestamp from a previous run that's newer than your test files | Delete the `last-sync` file from the storage container to force the program to copy everything again from scratch |

## Appendix A: SharePoint orientation and permissions

This appendix goes deeper than step 5.4 — read it if you want to
understand *why* the SharePoint steps work the way they do, not just
*what* to click.

### A.1 Anatomy of a SharePoint site

A SharePoint **tenant** is your organization's whole SharePoint
environment — `https://hydroone.sharepoint.com` is an example of one. A
**site** is one project/team space inside that tenant, identified by a
**site path** like `/sites/HODS`. Put together, the full address of a
site looks like:

```
https://hydroone.sharepoint.com/sites/HODS
        \_________ hostname _________/\__ site path __/
```

This program needs the hostname and site path as two *separate* values
(`SHAREPOINT_SITE_HOSTNAME` and `SHAREPOINT_SITE_PATH`) — the easiest way
to get them right is to open the site in a browser, look at the address
bar, and split the URL at the third `/` as shown above. Don't include
`https://` in the hostname, and do include the leading `/` in the site
path.

Inside a site, a **document library** is a folder-like area for storing
files (the default one is usually called "Documents," but sites often
have more than one). Microsoft Graph (the API this program uses) calls a
document library a **drive** — that's why the setting is named
`SHAREPOINT_LIBRARY_DRIVE_NAME` instead of "library name." A library's
*display name* (what you see in the SharePoint UI) and its *internal*
name are sometimes different — if you get a "Drive not found" error
despite the name looking right, check **Library Settings** (gear icon →
**Library settings**, or **Settings** in the library's own toolbar) for
the exact internal **Name** field.

A **column** is a piece of metadata attached to each file in a library
(e.g. "Department," "Document Type") — visible as extra fields/headers in
the library's list view. This program can copy the value of *one* column
per file into the uploaded blob's metadata, via
`SHAREPOINT_METADATA_COLUMN` (step 5.4 mentions adding one if you're
setting up a test site from scratch).

### A.2 SharePoint permission levels, and how to check your own

SharePoint has its own permission system, separate from both Entra ID and
Azure RBAC (more on that distinction in Appendix B.4). The main levels,
from least to most access, are:

| Level | Can do |
|---|---|
| **Visitor** | View/download files only |
| **Member** | View, upload, edit, delete files |
| **Owner** | Everything Member can, plus manage the site's own permissions and settings |
| **Site collection administrator** | Full control over the site and everything in it, set by a tenant admin |

**To check your own level on a site:** click the gear icon (top right) →
**Site permissions** (on some sites: **Site contents** → **Site
settings** → **Site permissions**). It lists the groups you belong to and
what level each has.

**To check what level the service principal (SPN) app needs:** this
project doesn't use a SharePoint-level permission at all for the app — it
uses a Microsoft Graph **application permission** (`Sites.Read.All` or
similar), granted in Entra ID and consented to by a tenant admin (step
5.4's "Register the Entra ID app" steps). That's a different, broader
mechanism: it lets the app read *any* site the permission covers, rather
than being added as a "Visitor" to one specific site. If your
organization's security policy requires the narrower, site-scoped version
instead of tenant-wide `Sites.Read.All`, ask your Entra ID admin about
Graph's site-scoped application permissions (sometimes set up via
PowerShell or the SharePoint admin center) — this is the "more limited,
site-specific permission" mentioned as an alternative in step 5.4 and
again in section 4.6's "least privilege" note.

### A.3 Common SharePoint-side errors

These complement the Troubleshooting table above, which covers what the
*program* reports — these are things you might see while clicking around
the SharePoint site itself:

| Symptom | Likely cause |
|---|---|
| "Sorry, this site hasn't been shared with you" when opening the site URL | Your own account doesn't have at least Visitor access to the site — ask a site Owner to add you (this only affects you browsing the site in a browser, not the app, which uses its own Entra ID credentials) |
| Library Settings shows a different "Name" than what's in the page title/breadcrumb | The library was renamed after creation — SharePoint keeps the original internal name in some contexts. Use the **Name** field from Library Settings for `SHAREPOINT_LIBRARY_DRIVE_NAME` |
| A column you added doesn't show up in uploaded blob metadata | Double-check `SHAREPOINT_METADATA_COLUMN` matches the column's *internal* name, which can differ from its display name if you renamed it after creating it (hover the column header → column settings to check) |

## Appendix B: Azure Portal orientation and RBAC deep-dive

### B.1 How the Portal is laid out

When you open [portal.azure.com](https://portal.azure.com), four areas
matter most:

- **Search bar (top center)** — the fastest way to get anywhere. Type a
  resource name, a resource type ("function app"), or a service name
  ("key vault") and pick from the results. Faster than clicking through
  menus.
- **Subscription/directory switcher (top right)** — if your account has
  access to more than one subscription or tenant, this controls which
  one's resources you're currently seeing. If a resource you expect to see
  isn't showing up anywhere, check this first.
- **Left-hand resource menu** — once you're on a specific resource (a
  Function App, a Storage account, etc.), this menu is specific to that
  resource type. This is why a Function App's menu has **Functions** /
  **Durable Functions** and a Storage account's menu has **Containers** /
  **Access keys** — they're different menus for different resource types
  (this is the same distinction used in step 8.1 to tell a Function App
  apart from a Web App).
- **Breadcrumb trail (top left of the main panel)** — shows where you are
  (e.g. `hods-rg > az-func-hodsai-dev-cae-001 > Environment variables`).
  Click any earlier part of it to jump back up a level instead of using
  the browser's back button (which can sometimes lose your place in a
  multi-step Portal form).

### B.2 The resource hierarchy

Azure organizes everything in a strict hierarchy, top to bottom:

```
Subscription          (billing boundary — "the account this is all under")
  └─ Resource group    (a named folder grouping related resources)
       └─ Resource      (the actual thing: a Function App, a Storage account, etc.)
```

For this project, everything lives in one resource group (commonly
`hods-rg`) inside one subscription. Searching for the resource group
first (as step 8.1 does) is usually faster than searching for an
individual resource by name, especially if you don't remember its exact
name.

### B.3 Resource types you'll encounter in this project

| Icon/name you'll see | What it is | Where it shows up in this guide |
|---|---|---|
| **Function App** | Runs this project's code | Steps 8.1–8.7 |
| **Storage account** | Holds the uploaded files (and the Function App's own bookkeeping data) | Step 7, section 11.5 |
| **Key Vault** | Stores secrets safely | Section 4.3–4.4, section 11.4 |
| **App Service plan** | The underlying compute capacity the Function App runs on (you generally don't need to touch this directly) | Not directly referenced elsewhere in this guide |
| **Application Insights** | Collects logs and telemetry from the Function App | Step 8.6 |
| **Log Analytics workspace** | Stores the data Application Insights collects, queried with KQL | Step 8.6, section 11.6 |

### B.4 Three separate permission systems — don't confuse them

It's easy to assume "Azure permissions" is one system. It's actually
three, managed in different places, and having access in one doesn't
imply anything about the others:

1. **Azure RBAC** (role assignments via **Access control (IAM)**) —
   controls who can view/manage *Azure resources themselves*: Function
   Apps, storage accounts, Key Vaults, etc. This is what section 4.8
   covers.
2. **Microsoft Entra ID permissions** — a separate system that controls
   identity-related things: who can register apps, grant admin consent
   for API permissions, manage users/groups. Being a Contributor on a
   resource group (Azure RBAC) does **not** give you any Entra ID
   permissions, and vice versa — they're unrelated grants. Step 5.4's "needs
   Entra ID admin rights" callouts refer to this second system, not RBAC.
3. **SharePoint permissions** (Appendix A.2) — controls who can access a
   SharePoint *site's content* directly in a browser. Unrelated to both of
   the above; the SPN app reading SharePoint via Graph application
   permissions doesn't go through this system at all (see A.2).

If you're stuck on a permission error, first identify *which* of these
three systems it's coming from — the error message and where you
encountered it usually makes this clear (an Azure Portal "Forbidden" on a
resource page is #1; "admin consent required" in an app registration's API
permissions page is #2; "this site hasn't been shared with you" in a
browser is #3).

### B.5 RBAC scope and how role assignments are actually granted

Section 4.8 covered checking your *own* access. This is for understanding
or performing the grant itself, if you're the one with **Owner** access:

- Roles can be assigned at different **scopes**: management group,
  subscription, resource group, or a single resource — in that order from
  broadest to narrowest. A role assigned at a broader scope is
  automatically inherited by everything underneath it (e.g. **Contributor**
  on the `hods-rg` resource group applies to every resource inside it,
  without assigning it individually to each one).
- Prefer the narrowest scope that gets the job done — e.g. assign
  **Key Vault Secrets User** on the one Key Vault someone needs, rather
  than **Contributor** on the whole resource group, if reading one secret
  is all they need to do.
- **To grant a role** (requires **Owner**, or a role with
  `Microsoft.Authorization/roleAssignments/write` permission, on the
  scope in question): go to the resource/resource group/subscription →
  **Access control (IAM)** → **+ Add** → **Add role assignment** → pick
  the role from the list (use the search box — there are hundreds of
  built-in roles, but this project only needs the four from section 4.8's
  table) → **Members** tab → **+ Select members** → search for the
  person's name or email → **Review + assign**.
- Built-in roles (like all four in section 4.8) cover almost everything
  this project needs. Custom roles (hand-built combinations of specific
  permissions) exist for more advanced cases but aren't needed here.
