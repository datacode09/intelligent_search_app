# User Acceptance Testing (UAT) Guide — HODS Intelligent Search

## Overview

UAT verifies that the deployed HODS application meets business requirements from the perspective of a real Hydro One document searcher. Tests are performed by end users (or a QA analyst acting as one) against the production-equivalent environment using real SharePoint document data.

**Who runs this:** Business stakeholders, document coordinators, QA analysts — not developers.  
**Environment:** Production or a staging environment seeded with real SharePoint content.  
**Prerequisites:** MSAL authentication working (ISSUE-5 resolved); at least one ingest run completed with real documents indexed.

---

## UAT Entry Criteria

Before beginning UAT, confirm all of the following are true:

- [ ] UI is accessible at the Static Web App URL and displays the search form
- [ ] Logging in with an Entra ID account redirects back to the app without error
- [ ] At least 50 documents are indexed (verify in Azure Portal → AI Search → hods-index → Document count)
- [ ] `GET /health` returns `{"status": "ok"}`
- [ ] MSAL token acquisition is working (confirmed by developer — ISSUE-5 resolved)
- [ ] `DocumentUrl` links are real SharePoint URLs (confirmed by developer — ISSUE-4 resolved)

---

## UAT Test Cases

---

### UAT-01 — Basic Text Search

**Business need:** A user can type a plain-English question and get relevant documents back.

**Tester:** Any Hydro One staff member with an Entra ID account.

**Steps:**
1. Open the HODS UI URL in a browser.
2. Log in with your Hydro One Microsoft account when prompted.
3. In the search box, type: `grounding and bonding procedures`
4. Click **Search**.

**Expected results:**
- [ ] Results appear within 5 seconds.
- [ ] At least one result is clearly related to grounding or bonding.
- [ ] Each result shows a document title, a prefix badge (e.g., `HO`), a content type badge, and a text snippet.
- [ ] Text snippet contains highlighted words related to the query.

**Fail if:** No results appear, the page shows an error, or results are completely unrelated to the query.

---

### UAT-02 — Query Optimization

**Business need:** The system improves vague or poorly worded queries automatically.

**Steps:**
1. In the search box, type a short, vague query: `safety`
2. Wait 1–2 seconds without clicking Search.

**Expected results:**
- [ ] A small "Optimizing query…" indicator appears briefly.
- [ ] The query in the search box is updated to a more descriptive phrase (e.g., `safety equipment and practices grounding`).
- [ ] Keyword chips appear below the search box showing extracted keywords.

**Fail if:** The search box is cleared, the page errors, or the optimizer spinner never disappears.

---

### UAT-03 — Filter by Content Type

**Business need:** Users can narrow results to a specific document category.

**Steps:**
1. Search for `equipment standards` (no filters).
2. Note the number of results and their content types.
3. In the **Content Type** dropdown, select `Corporate Standards`.
4. Click **Search**.

**Expected results:**
- [ ] The result count decreases (or stays the same if all were Corporate Standards).
- [ ] Every result displayed has the **Corporate Standards** badge.
- [ ] No results from other content types appear.

**Fail if:** Results from other content types appear with the filter applied.

---

### UAT-04 — Filter by Prefix

**Business need:** Users working in a specific region or department can restrict results to their area.

**Steps:**
1. Search for `maintenance procedures` with no filters.
2. In the **Prefix** dropdown, select `HO`.
3. Click **Search**.

**Expected results:**
- [ ] All results show an `HO` prefix badge.
- [ ] No results with other prefixes (AL, BU, etc.) appear.

**Fail if:** Results from other prefixes appear.

---

### UAT-05 — Combined Filters

**Business need:** Users can apply multiple filters simultaneously.

**Steps:**
1. Search for `inspection` with Content Type = `Bulletins` AND Prefix = `SP`.
2. Click **Search**.

**Expected results:**
- [ ] All results have prefix `SP` AND content type `Bulletins`.
- [ ] If no documents match, the message "No results found." is displayed — this is acceptable.

---

### UAT-06 — Document Link Opens in SharePoint

**Business need:** Clicking a result title opens the original document in SharePoint.

**Steps:**
1. Run any search that returns results.
2. Click on the title of the first result (should appear as a hyperlink).

**Expected results:**
- [ ] A new browser tab opens.
- [ ] The document opens in SharePoint or downloads directly.
- [ ] The correct document is displayed (matches the result title).

**Fail if:** The link navigates to `#` (stub URL — indicates ISSUE-4 not resolved), opens a 404, or opens the wrong document.

---

### UAT-07 — No Results Handled Gracefully

**Business need:** Searching for something that doesn't exist should not crash the app.

**Steps:**
1. Search for a nonsense string: `xkq7zzz99nonexistent`.

**Expected results:**
- [ ] The message "No results found." appears.
- [ ] No error banner or exception is shown.
- [ ] The search form remains usable for a new search.

---

### UAT-08 — Extractive Answers (Best Answer)

**Business need:** For specific factual questions, the system should surface the most relevant passage directly.

**Steps:**
1. Search for: `what is the minimum clearance for high voltage equipment`

**Expected results:**
- [ ] At least one result contains a highlighted text snippet with the relevant passage.
- [ ] The highlighted text is a coherent sentence or phrase, not a random word.

**Note:** Extractive answers are a "best effort" feature — if no confident answer exists, results without answers are acceptable.

---

### UAT-09 — Session Persistence (Login Stays Active)

**Business need:** Users should not need to re-authenticate for every search.

**Steps:**
1. Log in and perform 3 different searches.
2. Wait 5 minutes without interacting with the page.
3. Perform a 4th search.

**Expected results:**
- [ ] All 4 searches return results without a login prompt.
- [ ] If the token expires silently, the app acquires a new one automatically (MSAL silent refresh).

**Fail if:** The user is redirected to a login page mid-session.

---

### UAT-10 — Performance Under Normal Load

**Business need:** Search results appear within an acceptable time for standard use.

**Steps:**
1. Perform 5 different searches in succession.
2. Time each search from clicking Search to results appearing.

**Expected results:**
- [ ] Each search completes in under 5 seconds.
- [ ] Query optimization completes in under 3 seconds.
- [ ] No timeout errors appear.

**Fail if:** Any search takes longer than 10 seconds or times out.

---

### UAT-11 — Mobile / Tablet Accessibility

**Business need:** The app is usable on a tablet or mobile device.

**Steps:**
1. Open the HODS UI on a tablet or in a mobile browser (or resize the desktop browser to 768px wide).
2. Perform a search and apply a Content Type filter.

**Expected results:**
- [ ] The search form is visible and usable without horizontal scrolling.
- [ ] Results are readable.
- [ ] Content Type dropdown opens and options are selectable.

---

## UAT Sign-Off Criteria

UAT is considered **passed** when:

- All critical test cases (UAT-01 through UAT-07) pass without workarounds
- No P1 defects (application error, crash, data loss, security breach) are open
- At least 2 business stakeholders have reviewed results and signed off

UAT is considered **blocked** if:
- ISSUE-4 (DocumentUrl) is not resolved — UAT-06 will fail
- ISSUE-5 (MSAL) is not resolved — all tests requiring login will fail

---

## UAT Defect Classification

| Priority | Description | Example |
|---|---|---|
| P1 — Critical | App cannot be used; data exposed; authentication broken | Login fails, all searches return 500, document links open wrong user's files |
| P2 — High | Core feature broken; no workaround | Filters produce wrong results, search returns no results for known documents |
| P3 — Medium | Feature degraded; workaround exists | Query optimizer fails but manual search works, links open in same tab not new tab |
| P4 — Low | Cosmetic or minor inconvenience | Typo in UI, spinner doesn't disappear on slow connection |

---

## UAT Sign-Off Form

| Test Case | Tester | Date | Result (Pass/Fail/Blocked) | Notes |
|---|---|---|---|---|
| UAT-01 Basic Search | | | | |
| UAT-02 Query Optimization | | | | |
| UAT-03 Content Type Filter | | | | |
| UAT-04 Prefix Filter | | | | |
| UAT-05 Combined Filters | | | | |
| UAT-06 Document Link | | | | |
| UAT-07 No Results | | | | |
| UAT-08 Extractive Answers | | | | |
| UAT-09 Session Persistence | | | | |
| UAT-10 Performance | | | | |
| UAT-11 Mobile/Tablet | | | | |

**UAT Lead sign-off:**  
Name: _______________________ Date: ___________  
Signature: _______________________

**Business Owner sign-off:**  
Name: _______________________ Date: ___________  
Signature: _______________________
