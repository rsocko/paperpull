# Eversource - Massachusetts i-doxs

Downloads provider-generated electric and gas bill PDFs from
`secure8.i-doxs.net/Eversource/Secure/AccountHistory.aspx`. This implementation
is based on observed portal behavior and synthetic tests. **A live
PaperPull/CDP pilot is still required.** Other Eversource portals and regions
are not supported.

## Setup and use

Run the repository's `setup-all.bat` (Windows) or `setup-all.command` (macOS).
A standalone app uses its own `setup.bat` / `setup.command` instead.
Copy `config.example.json` to your private `config.json`, set `owner` and
`output_dir`, and leave the browser port at **9308** unless it is occupied.
Do not share config, browser profiles, state, logs or downloaded PDFs.

Open `login.bat` / `login.command`, sign in manually, complete any security
checks yourself, open Account History and leave that browser open. Then,
from the repository root:

```text
paperpull eversource discover
paperpull eversource pilot --year 2031
paperpull eversource all --start-date 2031-01-01 --end-date 2031-12-31
paperpull eversource resume
paperpull eversource verify
```

The dates above are synthetic examples; substitute your desired period.
Inspect the pilot PDFs before running All. Repeating Pilot skips successful
records, including PDFs deleted after import into an archive (the repository's
sticky delete-safe convention). Use `--redownload` with a scoped Pilot/All to
rebuild missing or invalid PDFs. Failed or review captures are not success
markers and remain retryable without this flag.

## What is automated

Every individual account listed by the account picker is visited, including
retired accounts; ALL is not treated as an account. Bills, all service types
and all statuses are selected explicitly. The portal advertises up to
**36 months** of history and reportedly limits date searches to 14 months.
Discovery uses contiguous windows of at most 12 months and walks numeric
pagination on each. `--year`, date bounds and `default_start_date` narrow
discovery through the existing period-filter convention.

Account, service/document type, issue date, amount and a duplicate occurrence
number distinguish bills. Same-day bills are not merged. Tokenized BillView
and iframe URLs are never stored as document IDs. Every download reacquires
the account/date filters and fresh page-local VIEW target, since returning
from BillView resets filters. Only the actual PDF source observed in
`#main_printPdf` is fetched, with allowlisted redirects and core atomic
delivery, PDF validation, indexing and progress.
Same-day amount competitors and other issue dates are checked separately,
so evidence for one cannot mask a PDF belonging to a neighboring bill.

Filters require their own recognized labels, IDs and values. Apply Dates,
numeric page links and VIEW pass guarded control handling; PAY and
CANCEL/EDIT PAY are never fallbacks. If a picker label, row layout, pager or
empty-list message differs from the observed shape, the run stops for a
survey rather than replaying raw WebForms or reporting an incomplete list
as complete. `diagnose` / `record` use the repository's standard diagnostics;
keep their private artifacts local.

## Validation and live pilot

Automated tests use synthetic browser pages and generated PDFs only:

```text
python -m pytest apps\eversource\tests -q
python -m pytest core -k "eversource or nationalgrid" -q
```

They are not proof of a real portal run. For live validation, use the app's
own CDP sign-in browser, run Discover for a known period, compare all accounts
and numeric pages with the portal, then run Pilot, open every PDF and repeat
Pilot. Test Resume after stopping a run and signing in again if required.
History older than the portal's retention must be obtained from Eversource;
no older PDFs are synthesized. Re-run All periodically after manual sign-in
to streamline future downloads; no scheduler or unattended login is added.
