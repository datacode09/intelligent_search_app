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

### 5.2 Install Node.js

This is needed later for the local storage emulator (Azurite — see the
glossary above). Download it from
[nodejs.org](https://nodejs.org/) (the "LTS" version is fine). To check:

```bash
node --version
```

Any version number printed back means it's installed.

### 5.3 Install Azure Functions Core Tools

This is the program that actually runs the Azure Function on your laptop.
It needs Node.js (from the previous step) to install:

```bash
npm install -g azure-functions-core-tools@4
```

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

You'll need **two terminal windows open at the same time** for this step:
one running the fake storage service, and one running the actual function.

**In your first terminal**, start Azurite (the fake storage service):

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
3. Inside the resource group, look for a resource whose name contains
   `ingest` and has the type "Function App" — that's the one you want.
   Click it.

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

If you don't have the Azure Functions Core Tools installed (step 5.3) and
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
that only the first run actually uploads anything. It needs Node.js
(already installed in step 5.2) to start that temporary Azurite copy:

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
