"""Massachusetts residential GAS only; live PaperPull/CDP pilot required.

The original signed-in tab must remain alive. Each bill's own button is
pressed once to observe its real popup route. That observed, validated route
is loaded in a temporary same-origin frame of the original tab, retaining
the SPA session, and its nested viewer's actual blob is read while alive.
No fabricated PDF routes, window.open patches or capability-test blobs.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlsplit

from paperpull_core import controls, dates, delivery, intake, pressing
from paperpull_core.identity import Identity
from paperpull_core.urls import is_safe_url as _safe_url
from paperpull_core.words import words_for

log = logging.getLogger("nationalgrid_docs.site")
SESSION_IN_TAB = True
BASE = "https://myaccount.nationalgrid.com"
BILLING_URL = BASE + "/bill-history"
URLS = {"home": BASE, "documents": BILLING_URL,
        "login": BASE + "/s/login/?region=massgas&customer_type=home"}
ALLOWED_HOSTS = {"myaccount.nationalgrid.com"}
LOGIN_URL_MARKERS = ["/login", "/signin", "/sign-in", "/auth", "/mfa", "/challenge"]
RATE_LIMIT_MARKERS = ["too many requests", "rate limit", "try again later", "unusual traffic"]
FORBIDDEN_CONTROL_RE = re.compile(
    r"\b(pay|payments?|autopay|auto[\s-]*pay|enroll|unenroll|budget\s+billing|"
    r"paperless|transfer|buy|sell|redeem|donate|submit|confirm|authorize|"
    r"cancel|edit|change|update|delete|remove|settings?|preferences?|profile|"
    r"password|start\s+service|stop\s+service|meter\s+readings?)\b", re.I)
SAFE_DOC_CONTROL_RE = re.compile(
    r"^(?:view(?:\s+(?:current|previous))?\s+bill(?:\s+pdf)?|"
    r"download\s+(?:pdf|statement)|credit\s+card\s+statement)$", re.I)
CURRENT = '[data-testid="view-current-bill-pdf-button"]'
PREVIOUS = '[data-testid^="view-previous-bill-pdf-button-"]'
FALLBACK = {"doc_link": f"{CURRENT}, {PREVIOUS}", "doc_row": f"tr:has({PREVIOUS})"}
EMPTY_RE = re.compile(r"\bno (?:bills?|bill history|statements?) (?:available|found|to display)\b", re.I)
RETENTION = "National Grid displays up to 2 years of bill history. Contact Customer Service for prior bills."


@dataclass
class RawDoc:
    title: str
    date_text: str
    account: str
    document_id: str
    row_index: int = -1
    href: str = ""
    text: str = ""


def is_safe_url(url):
    return _safe_url(url, ALLOWED_HOSTS, subdomains=False)


def is_safe_control(name):
    return bool(name and not FORBIDDEN_CONTROL_RE.search(name)
                and not controls.SETTINGS_CONTROL_RE.search(name)
                and not controls.AUTH_CONTROL_RE.search(name)
                and SAFE_DOC_CONTROL_RE.fullmatch(name.strip()))


def parse_date(text):
    patterns = (r"\b\d{4}-\d{2}-\d{2}(?!\d)", r"\b\d{1,2}/\d{1,2}/\d{4}\b",
                r"\b[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}\b")
    for pattern in patterns:
        for token in re.findall(pattern, text or ""):
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y",
                        "%B %d %Y", "%b %d %Y"):
                try:
                    return dates.checked(datetime.strptime(token, fmt).date().isoformat())
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
            "National Grid sign-in expired or requires attention; sign in and resume."])


def _account_link(page):
    if not is_safe_url(page.url) or urlsplit(page.url).path != "/bill-history":
        raise RuntimeError("Open Massachusetts residential gas Bill History in the signed-in tab.")
    values = parse_qs(urlsplit(page.url).query).get("accountLink", [])
    if len(values) != 1 or not values[0]:
        raise RuntimeError("Bill History does not identify one selected account.")
    return values[0]


def goto_documents(page):
    # Never page.goto here: opening an address in another tab loses this session.
    _session(page)
    _account_link(page)
    page.wait_for_function(
        """arg => {
          const main = document.querySelector('main');
          if (!main) return false;
          const text = main.innerText;
          const empty = /no (bills?|bill history|statements?) (available|found|to display)/i.test(text);
          const retention = /up to (2|two) years? of bill history/i.test(text);
          return empty || (main.querySelector(arg.selector) && retention);
        }""",
        arg={"selector": FALLBACK["doc_link"]}, timeout=15000)
    _session(page)
    return bool(page.locator(FALLBACK["doc_link"]).count() or
                EMPTY_RE.search(page.locator("main").inner_text()))


_ROW_JS = r"""el => {
  const selector = '[data-testid="view-current-bill-pdf-button"], [data-testid^="view-previous-bill-pdf-button-"]';
  for (let n = el.parentElement, depth = 0; n && depth < 8; n = n.parentElement, depth++) {
    if (n.querySelectorAll(selector).length !== 1) return '';
    const text = n.innerText || '';
    if (/\d{4}-\d{2}-\d{2}|\d{1,2}\/\d{1,2}\/\d{4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}/.test(text))
      return text;
  }
  return '';
}"""


def _issue_date(text):
    labelled = re.search(
        r"(?:issue(?:d)?(?:\s+on)?|bill(?:ing)?|statement)\s*date\s*:?\s*"
        r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})",
        text or "", re.I)
    if labelled:
        return parse_date(labelled.group(1))
    tokens = re.findall(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{4}\b|"
                        r"\b[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}\b", text or "")
    found = {parse_date(t) for t in tokens}
    if len(found) != 1 or None in found or re.search(r"\bdue\s+date\b", text or "", re.I):
        raise RuntimeError("Bill row has no unambiguous visible issue date.")
    return found.pop()


def _identity(account_link, iso):
    account = hashlib.sha256(account_link.encode()).hexdigest()
    return account, hashlib.sha256(f"{account}:Gas Bill:{iso}".encode()).hexdigest()


def collect_download_docs(page, keep=None, start=None, end=None):
    if not goto_documents(page):
        raise RuntimeError("Bill History did not finish rendering.")
    print(RETENTION)
    link = _account_link(page)
    out = []
    loc = page.locator(FALLBACK["doc_link"])
    seen = set()
    for i in range(loc.count()):
        button = loc.nth(i)
        iso = _issue_date(button.evaluate(_ROW_JS))
        testid = button.get_attribute("data-testid") or ""
        if testid != "view-current-bill-pdf-button" and testid != \
                f"view-previous-bill-pdf-button-{iso}":
            raise RuntimeError("Bill button date disagrees with its visible row issue date.")
        if iso in seen:
            raise RuntimeError("Two bills have the same issue date and cannot be told apart by the portal's controls.")
        seen.add(iso)
        if keep and not keep(iso[:4]):
            continue
        if (start and iso < start) or (end and iso > end):
            continue
        account, document_id = _identity(link, iso)
        out.append(RawDoc("Gas Bill", iso, account, document_id, i))
    if not out and not loc.count() and not EMPTY_RE.search(page.locator("main").inner_text()):
        raise RuntimeError("Blank Bill History is not an empty list.")
    # Do not silently truncate an implementation built from a non-paginated list.
    more = page.get_by_role("button", name=re.compile(r"^(?:load|show|view)\s+more(?:\s+bills)?$|^next(?:\s+page)?$", re.I))
    if any(more.nth(i).is_visible() and more.nth(i).is_enabled() for i in range(more.count())):
        raise RuntimeError("Bill History has additional pages; survey is required before claiming completeness.")
    return out


def _view_route(url, account_link, iso, kind):
    if not is_safe_url(url) or urlsplit(url).path != "/bill-history/viewbillpdf":
        return False
    query = parse_qs(urlsplit(url).query)
    return (query.get("accountLink") == [account_link] and
            query.get("date") == [iso] and query.get("billType") == [kind])


def _blob_from_viewer(url):
    if not is_safe_url(url) or urlsplit(url).path != \
            "/_uwp2/pdf/resource/ng_pdfjs3/web/viewer.html":
        return ""
    files = parse_qs(urlsplit(url).query).get("file", [])
    if len(files) != 1 or not files[0].startswith(f"blob:{BASE}/"):
        return ""
    return files[0]


_FETCH_BLOB_JS = """async url => {
  const response = await fetch(url, {redirect:'error'});
  if (!response.ok) throw new Error('PDF blob could not be read');
  const bytes = await ppRead(response);
  if (bytes.length < 5 || String.fromCharCode(...bytes.slice(0,5)) !== '%PDF-')
    throw new Error('Bill viewer did not return a PDF');
  let text = '';
  for (let i=0; i<bytes.length; i+=0x8000)
    text += String.fromCharCode(...bytes.subarray(i,i+0x8000));
  return btoa(text);
}"""


def _read_viewer_blob(page, route):
    name = "paperpull-bill-" + uuid.uuid4().hex
    page.evaluate("""({name, url}) => {
      const frame = document.createElement('iframe');
      frame.name = name; frame.id = name; frame.src = url;
      frame.style.cssText = 'position:fixed;left:0;top:0;width:1px;height:1px;pointer-events:none';
      document.body.appendChild(frame);
    }""", {"name": name, "url": route})
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            _session(page)
            root = page.frame(name=name)
            if root:
                if root.url not in ("", "about:blank") and not _view_route(
                        root.url, parse_qs(urlsplit(route).query)["accountLink"][0],
                        parse_qs(urlsplit(route).query)["date"][0],
                        parse_qs(urlsplit(route).query)["billType"][0]):
                    raise RuntimeError("Bill frame redirected away from its observed document route.")
                for child in root.child_frames:
                    blob = _blob_from_viewer(urljoin(root.url, child.url))
                    if blob:
                        data = intake.decode(intake.evaluate(child, _FETCH_BLOB_JS, blob))
                        if not data.startswith(b"%PDF-"):
                            raise RuntimeError("Bill viewer returned an empty or non-PDF blob.")
                        return data
            page.wait_for_timeout(200)
        raise RuntimeError("Bill frame never rendered its same-origin PDF viewer; sign in again if expired.")
    finally:
        if not page.is_closed() and is_safe_url(page.url):
            page.evaluate("name => document.getElementById(name)?.remove()", name)


def download_document(page, doc, out_path):
    rows = collect_download_docs(page)
    matches = [r for r in rows if r.document_id == doc.document_id and r.account == doc.account]
    if len(matches) != 1:
        raise RuntimeError("Selected account or listed bill changed; rediscover before resuming.")
    row = matches[0]
    button = page.locator(FALLBACK["doc_link"]).nth(row.row_index)
    labels = controls.control_labels(button)
    if not labels or any(not is_safe_control(label) for label in labels):
        raise RuntimeError("Bill button's own labels were refused.")
    kind = "current" if button.get_attribute("data-testid") == \
        "view-current-bill-pdf-button" else "past"
    account_link = _account_link(page)
    popup = None
    observed = []

    def observe(request):
        if request.is_navigation_request() and is_safe_url(request.url) and \
                urlsplit(request.url).path == "/bill-history/viewbillpdf":
            observed.append(request)

    page.context.on("request", observe)
    try:
        with page.expect_popup(timeout=15000) as opened:
            pressing.click(page, button, css=FALLBACK["doc_link"],
                           what="the listed bill's own View Bill button",
                           words=words_for("National Grid"), step="open the bill PDF")
        popup = opened.value
        routes = list(dict.fromkeys(r.url for r in observed if r.frame.page == popup))
        if not routes and _view_route(popup.url, account_link, doc.date, kind):
            routes = [popup.url]
        if len(routes) != 1:
            raise RuntimeError("The bill popup did not identify exactly one provider document route.")
        route = routes[0]
        if not _view_route(route, account_link, doc.date, kind):
            raise RuntimeError("Observed bill route disagrees with the listed account, issue date or bill type.")
        data = _read_viewer_blob(page, route)
        result = delivery.place(
            data, out_path, expect=Identity(date=doc.date, kind="Gas Bill"),
            rivals=tuple(Identity(date=r.date_text, kind=r.title)
                         for r in rows if r.document_id != row.document_id))
        if not result.ok:
            log.warning("Bill delivery refused: %s", result.say())
        return result.ok
    finally:
        page.context.remove_listener("request", observe)
        if popup is not None:
            popup.close()
