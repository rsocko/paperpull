"""Massachusetts i-doxs bill history. Live PaperPull/CDP pilot still required.

Only the history's filters, numeric pager and each row's own VIEW are used.
WebForms postbacks are made by the site's controls, never replayed as forms.
BillView/iframe addresses are transient and never become document identities.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, urlsplit

from paperpull_core import controls, dates, delivery, identity, intake, pressing, redirects
from paperpull_core.identity import Identity
from paperpull_core.urls import is_safe_url as _safe_url
from paperpull_core.words import words_for

log = logging.getLogger("eversource_docs.site")
SESSION_IN_TAB = False
BASE = "https://secure8.i-doxs.net"
BILLING_URL = BASE + "/Eversource/Secure/AccountHistory.aspx"
URLS = {"home": BILLING_URL, "login": BILLING_URL, "documents": BILLING_URL}
ALLOWED_HOSTS = {"secure8.i-doxs.net"}
LOGIN_URL_MARKERS = ["/login", "/signin", "/sign-in", "/auth", "/mfa", "/challenge"]
RATE_LIMIT_MARKERS = ["too many requests", "rate limit", "try again later", "unusual traffic"]
FORBIDDEN_CONTROL_RE = re.compile(
    r"\b(pay|payments?|autopay|auto[\s-]*pay|enroll|unenroll|budget\s+billing|"
    r"paperless|transfer|buy|sell|redeem|donate|submit|confirm|authorize|"
    r"cancel|edit|change|update|delete|remove|settings?|preferences?|profile|"
    r"password|start\s+service|stop\s+service|meter\s+readings?)\b", re.I)
SAFE_DOC_CONTROL_RE = re.compile(
    r"^(?:view(?:\s+(?:bill|statement|pdf))?|download(?:\s+(?:bill|statement|pdf))?|"
    r"credit\s+card\s+statement)$", re.I)
FALLBACK = {
    "doc_row": "tr:has(a[id*='_viewBtn_'])",
    "doc_link": "a[id^='main_lvAccountHistory_viewBtn_'][href*='viewBtn']",
    "pager": "a[href*='dpAccountHistory']",
}
EMPTY_RE = re.compile(r"\b(no (?:records|bills|transactions|history)(?: were)? found|"
                      r"no (?:records|bills|transactions) to display)\b", re.I)


@dataclass
class RawDoc:
    title: str
    date_text: str
    account: str
    document_id: str
    row_index: int = -1
    href: str = ""
    text: str = ""
    total: str = ""


def is_safe_url(url):
    return _safe_url(url, ALLOWED_HOSTS, subdomains=False)


def is_document_url(url):
    return is_safe_url(url) and urlsplit(url).path.lower() == \
        "/eversource/secure/__imagegrabber.axd"


def is_safe_control(name):
    return bool(name and not FORBIDDEN_CONTROL_RE.search(name)
                and not controls.SETTINGS_CONTROL_RE.search(name)
                and not controls.AUTH_CONTROL_RE.search(name)
                and SAFE_DOC_CONTROL_RE.fullmatch(name.strip()))


def parse_date(text):
    for token in re.findall(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})(?!\d)", text or ""):
        for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                iso = datetime.strptime(token, fmt).date().isoformat()
                return dates.checked(iso)
            except ValueError:
                continue
    return None


def parse_period_date(text):
    return parse_date(text), ""


def looks_signed_out(page):
    return (not is_safe_url(page.url) or
            any(m in urlsplit(page.url).path.lower() for m in LOGIN_URL_MARKERS) or
            page.locator("input[type=password]").count() > 0)


def detect_security_challenge(page):
    body = page.locator("body").inner_text(timeout=5000).lower()
    if any(s in body for s in ("verification code", "verify your identity", "captcha",
                               "session has expired", "are you human", *RATE_LIMIT_MARKERS)):
        return "Sign-in expired or a security check is required; resolve it in the browser."
    return None


def _session(page):
    if looks_signed_out(page) or detect_security_challenge(page):
        raise pressing.Unread("read bill history", "sign in is required", [
            "Eversource sign-in expired or requires attention; sign in and resume."])


def _ready(page):
    _session(page)
    if urlsplit(page.url).path.lower() != "/eversource/secure/accounthistory.aspx":
        raise RuntimeError("Eversource Account History is not open.")
    page.locator("#main_ddlAccount").wait_for(state="visible", timeout=15000)
    page.wait_for_load_state("networkidle", timeout=15000)
    if not (page.locator(FALLBACK["doc_link"]).count()
            or EMPTY_RE.search(page.locator("body").inner_text())):
        raise RuntimeError("Bill list has not rendered; an empty page is not an empty history.")


def goto_documents(page):
    if not is_safe_url(page.url) and page.url != "about:blank":
        raise RuntimeError("Refusing to navigate a tab outside Eversource.")
    if page.url != BILLING_URL:
        page.goto(BILLING_URL, wait_until="domcontentloaded")
    _ready(page)
    return True


def _guarded_filter(page, selector, allowed_labels):
    _session(page)
    loc = page.locator(selector)
    if loc.count() != 1 or not loc.is_visible():
        raise RuntimeError("Required history filter is missing or ambiguous.")
    identity = controls.control_identity(loc)
    labels = loc.evaluate("""el => {
      const by = el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      return [el.getAttribute('aria-label'), el.getAttribute('title'),
              by && by.innerText].filter(Boolean);
    }""")
    if (not identity or FORBIDDEN_CONTROL_RE.search(identity)
            or controls.AUTH_CONTROL_RE.search(identity)
            or not labels or any(not re.fullmatch(allowed_labels, t.strip(), re.I)
                                 for t in labels)):
        raise RuntimeError("History filter identity or its own label was refused.")
    return loc


def _select(page, selector, value, labels):
    loc = _guarded_filter(page, selector, labels)
    if loc.input_value() != value:
        loc.select_option(value=value)
        page.wait_for_load_state("networkidle", timeout=15000)
    if page.locator(selector).input_value() != value:
        raise RuntimeError("The history filter did not keep its selected value.")


def _press(page, loc, css, guard, what):
    labels = controls.control_labels(loc)
    if not labels or any(not guard(label) for label in labels):
        raise RuntimeError("History control's own labels were refused.")
    pressing.click(page, loc, css=css, what=what,
                   words=words_for("Eversource"), step="read bill history")


def _apply(page, account, start, end):
    goto_documents(page)
    _select(page, "#main_ddlAccount", account, r"(?:select\s+)?account(?:\s+(?:number|filter))?\s*:?")
    _select(page, "#main_ddlTypeFilter", "Bills", r"(?:document|transaction)?\s*type(?:\s+filter)?\s*:?")
    _select(page, "#main_ddlActTypeFilter", "ALL", r"(?:account|service|activity)\s*type(?:\s+filter)?\s*:?")
    _select(page, "#main_ddlStatusFilter", "ALL", r"status(?:\s+filter)?\s*:?")
    for selector, value, labels in (
            ("#main_txtStartDate", start, r"(?:start|from)\s*date\s*:?"),
            ("#main_txtEndDate", end, r"(?:end|to)\s*date\s*:?")):
        loc = _guarded_filter(page, selector, labels)
        loc.fill(date.fromisoformat(value).strftime("%m/%d/%Y"))
    _press(page, page.locator("#main_lnkApply"), "#main_lnkApply",
           lambda t: bool(re.fullmatch(r"apply\s+dates", t.strip(), re.I)),
           "the Apply Dates history filter")
    _ready(page)
    expected = {"#main_ddlAccount": account, "#main_ddlTypeFilter": "Bills",
                "#main_ddlActTypeFilter": "ALL", "#main_ddlStatusFilter": "ALL",
                "#main_txtStartDate": date.fromisoformat(start).strftime("%m/%d/%Y"),
                "#main_txtEndDate": date.fromisoformat(end).strftime("%m/%d/%Y")}
    if any(page.locator(s).input_value() != v for s, v in expected.items()):
        raise RuntimeError("History filters changed while applying dates; list is not trusted.")


def date_windows(start=None, end=None, today=None):
    today = today or date.today()
    floor = today.replace(year=today.year - 3, day=min(today.day, dates.last_day(today.year - 3, today.month)))
    first = max(date.fromisoformat(start), floor) if start else floor
    last = min(date.fromisoformat(end), today) if end else today
    while first <= last:
        next_year = first.replace(year=first.year + 1,
                                  day=min(first.day, dates.last_day(first.year + 1, first.month)))
        stop = min(last, next_year - timedelta(days=1))
        yield first.isoformat(), stop.isoformat()
        first = stop + timedelta(days=1)


def _accounts(page):
    goto_documents(page)
    options = page.locator("#main_ddlAccount option").evaluate_all(
        "els => els.map(e => ({value:e.value, disabled:e.disabled}))")
    accounts = [o["value"] for o in options if not o["disabled"] and
                o["value"] and o["value"].upper() != "ALL"]
    if not accounts or len(accounts) != len(set(accounts)):
        raise RuntimeError("Account picker is empty or ambiguous; cannot enumerate bill history.")
    return accounts


def _account_text(value):
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _rows(page, account, counts):
    loc = page.locator(FALLBACK["doc_link"])
    out = []
    for i in range(loc.count()):
        link = loc.nth(i)
        cells = link.locator("xpath=ancestor::tr[1]").locator("td").all_inner_texts()
        clean = [re.sub(r"\s+", " ", c).strip() for c in cells]
        found_dates = [parse_date(c) for c in clean if parse_date(c)]
        kinds = [c.title() for c in clean if c.lower() in ("electric", "gas")]
        amounts = [c for c in clean if re.fullmatch(r"\$?\s*-?[\d,]+\.\d{2}", c)]
        if (len(found_dates) != 1 or len(kinds) != 1 or len(amounts) != 1
                or not any(c.lower() == "bill" for c in clean)
                or not any(_account_text(c) == _account_text(account) for c in clean)):
            raise RuntimeError("A bill row's account, issue date, type or amount is ambiguous.")
        fingerprint = json.dumps([account, kinds[0], found_dates[0], amounts[0]])
        counts[fingerprint] += 1
        document_id = hashlib.sha256(
            f"{fingerprint}:{counts[fingerprint]}".encode()).hexdigest()
        out.append(RawDoc(f"{kinds[0]} Bill", found_dates[0], account, document_id,
                          i, total=amounts[0]))
    return out


def _pages(page):
    seen = set()
    for _ in range(500):
        _ready(page)
        # A numeric pager's current page is text, not a clickable link.
        pager = page.locator("[id$='dpAccountHistory']")
        markers = pager.inner_text() if pager.count() else ""
        signature = (markers, tuple(page.locator(FALLBACK["doc_row"]).all_inner_texts()))
        if signature in seen:
            raise RuntimeError("History pagination repeated; refusing to call the list complete.")
        seen.add(signature)
        yield
        links = page.locator(FALLBACK["pager"])
        targets = []
        for i in range(links.count()):
            label = links.nth(i).inner_text().strip()
            if label.isdecimal():
                targets.append((int(label), i))
        current = page.locator("[id$='dpAccountHistory'] span").all_inner_texts()
        numbers = [int(t.strip()) for t in current if t.strip().isdecimal()]
        if len(set(numbers)) != 1:
            if not targets:
                return
            raise RuntimeError("Cannot identify the history pager's current numeric page.")
        forward = sorted((n, i) for n, i in targets if n > numbers[0])
        if not forward:
            # A continuation without a numeric next page is not silently ignored.
            if any(t.strip() in (">", ">>", "...") or re.search(r"next", t, re.I)
                   for t in links.all_inner_texts()):
                raise RuntimeError("Pager has hidden later pages; a numeric-page survey is required.")
            return
        number, index = forward[0]
        _press(page, links.nth(index), FALLBACK["pager"],
               lambda t: t.strip() == str(number), "the next numeric history page")
    raise RuntimeError("History exceeded the pagination limit; the list is not complete.")


def collect_download_docs(page, keep=None, start=None, end=None):
    print("Eversource exposes up to 36 months of history; older bills are not collected.")
    out = []
    for account in _accounts(page):
        for first, last in date_windows(start, end):
            if keep and not any(keep(str(y)) for y in range(int(first[:4]), int(last[:4]) + 1)):
                continue
            _apply(page, account, first, last)
            counts = Counter()
            for _ in _pages(page):
                rows = _rows(page, account, counts)
                if any(not first <= r.date_text <= last for r in rows):
                    raise RuntimeError("History returned a bill outside the applied date window.")
                out.extend(rows)
    return out


def identity_for(row):
    return Identity(date=getattr(row, "date", "") or row.date_text,
                    total=row.total, kind=row.title, label=row.account)


def rival_pools(row, rows):
    """Compare same-day amounts separately from the account's other issue dates."""
    expected = identity_for(row)
    same_day, near = [], []
    for other in rows:
        if other.document_id == row.document_id or other.account != row.account:
            continue
        candidate = identity_for(other)
        if candidate.date == expected.date:
            same_day.append(candidate)
        elif other.title == row.title:
            near.append(Identity(date=candidate.date))
    return tuple(same_day), tuple(near)


def download_document(page, doc, out_path, *, known_docs=()):
    # Rediscover this day across every page: VIEW/back resets all the filters.
    _apply(page, doc.account, doc.date, doc.date)
    counts = Counter()
    for _ in _pages(page):
        rows = _rows(page, doc.account, counts)
        matches = [r for r in rows if r.document_id == doc.document_id]
        if not matches:
            continue
        row = matches[0]
        # Fresh rows override stored metadata; other pages remain competitors.
        competitors = {r.document_id: r for r in (*known_docs, *rows)}
        same_day, near = rival_pools(row, competitors.values())
        _press(page, page.locator(FALLBACK["doc_link"]).nth(row.row_index),
               FALLBACK["doc_link"], is_safe_control, "the bill row's own VIEW link")
        page.wait_for_url(re.compile(r"https://secure8[.]i-doxs[.]net/Eversource/Secure/BillView[.]aspx[?].*"))
        _session(page)
        iframe = page.locator("#main_printPdf")
        iframe.wait_for(state="attached")
        source = urljoin(page.url, iframe.get_attribute("src") or "")
        if not is_document_url(source):
            raise RuntimeError("Bill viewer did not name an allowlisted PDF source.")
        response = redirects.get(page.request, source, is_document_url)
        try:
            if not response.ok:
                raise RuntimeError("Bill PDF request failed or redirected outside the document allowlist.")
            data = intake.body(response)
        finally:
            response.dispose()
        result = delivery.place(
            data, out_path,
            expect=identity_for(row), rivals=same_day)
        if not result.ok:
            log.warning("Bill delivery refused: %s", result.say())
            return False
        # Amount evidence must not excuse a different issue date.
        again = identity.distinguish(out_path, Identity(date=doc.date), near)
        if again.outcome == identity.REFUSED:
            log.warning("Bill delivery refused: the issue date belongs to another bill.")
            out_path.unlink()
            return False
        return True
    raise RuntimeError("Listed bill no longer appears for its account/date; rediscover before resuming.")
