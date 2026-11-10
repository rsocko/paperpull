# National Grid - Massachusetts residential gas

Downloads the selected account's current and previous gas bill PDFs.
**Only Massachusetts residential GAS is supported.** Other regions, electric
accounts and business accounts have not been established by the available
portal evidence. The implementation has synthetic browser tests; **a live
PaperPull/CDP pilot is still required**.

## Setup and use

Run the repository's `setup-all.bat` (Windows) or `setup-all.command` (macOS),
or the app's own `setup.bat` / `setup.command` for a standalone installation.
Copy `config.example.json` to private `config.json`, set `owner` and
`output_dir`, and reserve CDP port **9309** for this provider.

Open `login.bat` / `login.command`. The login route specifies
`region=massgas&customer_type=home`; the generic myaccount root may default
to New York. Sign in manually, including redirects and security checks,
select the gas account and open **Bill History**. Leave that exact tab open:
the session may not survive navigation into a new tab. Do not copy
`accountLink`, credentials, cookies or browser state into source or fixtures.

From the repository root:

```text
paperpull nationalgrid discover
paperpull nationalgrid pilot
paperpull nationalgrid all --start-date 2031-01-01 --end-date 2031-12-31
paperpull nationalgrid resume
paperpull nationalgrid verify
```

The dates are synthetic examples; substitute your period. Re-running All
after manual sign-in streamlines later downloads without a scheduler or
credential automation. Successful documents remain skipped after deletion or
import elsewhere, following the repository's sticky delete-safe convention.
Use scoped `--redownload` to rebuild missing or invalid PDFs. Failed and review
captures remain retryable without it. With several accounts, run separately
for each selected account in the same profile.

## Session-preserving capture and limits

The app reads the actual rendered current bill and every previous-bill
button. Visible issue dates are authoritative and must agree with button
IDs and the outgoing PDF-view route. It does not infer a bill per calendar
month or assume a fixed bill count. A bill keeps its identity when it moves
from current to past. A different selected account cannot satisfy a stored
bill's identity.

Each bill's own guarded View button is pressed once. Normal Playwright popup
and request observation learns the actual provider route, even if the popup
loses sign-in. Only a route matching the selected dynamic account link,
issue date and current/past kind is loaded into a temporary same-origin
iframe of the original signed-in tab. The actual nested PDF viewer's file
blob is fetched while its generating frame remains alive, then passed to core
atomic delivery and identity validation. No window.open patches, empty
capability-test blobs, printed pages or manufactured PDF routes are used.
The frame, popup and request listener are cleaned up on success and failure.

National Grid explicitly displays **up to two years of bill history**.
Contact Customer Service for older bills; the app neither synthesizes them
nor claims calendar-month coverage proves completeness. The supported list
has no pagination. An unexpected enabled Load more/Next button stops
discovery for a survey rather than silently truncating it. A rendered bill
list with its retention footer, or an explicit rendered no-bills message,
confirms readiness; a current-bill button alone does not finish discovery.
An expired sign-in or challenge stops the run for manual recovery.

## Validation and live pilot

Synthetic browser tests exercise current/past routes, session-preserving
frames, distinct generated PDF blobs, StrictDelivery, unsafe controls and
invalid/empty captures:

```text
python -m pytest apps\nationalgrid\tests -q
python -m pytest core -k "eversource or nationalgrid" -q
```

These are not live-account tests. For a live pilot, sign in through this
app's CDP browser, compare Discover with the selected account's complete
visible list, then run Pilot and open every resulting PDF. Repeat Pilot to
confirm skips, stop and Resume a run, and verify that an expired session
requests manual sign-in. Keep configs, PDFs, state, logs and diagnostic
artifacts private. Earlier browser experiments do not establish PaperPull/CDP
integration.
