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

## 4. Security — please read before going further

- The file `local.settings.json` (you'll create it in the next section)
  holds real secrets — your SharePoint app's password (client secret), and
  possibly a real storage password (connection string). This file is set up
  to never be saved into the project's shared history (git). **Never**
  remove it from the `.gitignore` file, never paste its contents into any
  other file in the project, and never share it over chat, email, or a
  screenshot.
- If a client secret was ever shared over chat, email, or a screenshot,
  treat it as already compromised. Ask whoever manages Entra ID to create
  a new one for you (App registrations → your app → Certificates & secrets)
  before relying on the old one for anything beyond a quick, throwaway test.

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
exists.

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
