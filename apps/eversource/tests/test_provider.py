"""Synthetic DOM and generated PDFs only. No account or network access."""
import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from paperpull_core import delivery, pressing, redirects
from paperpull_core.models import State
from paperpull_core.testkit import StrictDelivery, text_pdf

import eversource_docs as engine
import eversource_site as site


HISTORY = """
<body><main>
<label for="main_ddlAccount">Account</label>
<select id="main_ddlAccount" onchange="render(1)">
<option>ALL</option><option>700001</option><option>700002</option><option>700003</option></select>
<label for="main_ddlTypeFilter">Document Type</label>
<select id="main_ddlTypeFilter"><option>Bills</option><option>Payments</option></select>
<label for="main_ddlActTypeFilter">Service Type</label>
<select id="main_ddlActTypeFilter"><option>ALL</option><option>Gas</option><option>Electric</option></select>
<label for="main_ddlStatusFilter">Status</label><select id="main_ddlStatusFilter"><option>ALL</option></select>
<label for="main_txtStartDate">Start Date</label><input id="main_txtStartDate">
<label for="main_txtEndDate">End Date</label><input id="main_txtEndDate">
<a id="main_lnkApply" href="javascript:render(1)">Apply Dates</a>
<table><tbody id="rows"></tbody></table><div id="empty"></div><div id="main_dpAccountHistory"></div>
</main><script>
const bills = [
 ['700001','Gas','02/14/2031','$12.34'],
 ['700001','Gas','02/14/2031','$56.78'],
 ['700001','Gas','02/14/2031','$56.78'],
 ['700001','Gas','03/18/2031','$19.23'],
 ['700002','Electric','02/14/2031','$23.45']
];
let current = [];
function render(n) {
 const account = document.querySelector('#main_ddlAccount').value;
 const start = document.querySelector('#main_txtStartDate').value;
 const end = document.querySelector('#main_txtEndDate').value;
 current = bills.filter(b => (account === 'ALL' || b[0] === account) &&
   (!start || new Date(b[2]) >= new Date(start)) && (!end || new Date(b[2]) <= new Date(end)));
 const rows = current.slice((n-1)*2, n*2);
 document.querySelector('#rows').innerHTML = rows.map((b,i) => '<tr>' +
   b.map(c => '<td>'+c+'</td>').slice(0,3).join('') + '<td>Bill</td><td>'+b[3]+'</td>' +
   '<td>Unpaid</td><td><a id="main_lvAccountHistory_viewBtn_'+i+
   '" href="javascript:showBill('+i+',\\'viewBtn\\')">VIEW</a> ' +
   '<button onclick="window.unsafe=true">PAY</button><button>CANCEL/EDIT PAY</button></td></tr>').join('');
 document.querySelector('#empty').innerText = current.length ? '' : 'No records found';
 const total = Math.ceil(current.length/2);
 document.querySelector('#main_dpAccountHistory').innerHTML = total < 2 ? '' :
   Array.from({length:total},(_,i) => i+1 === n ? '<span>'+n+'</span>' :
   '<a href="javascript:render('+(i+1)+',\\'dpAccountHistory\\')">'+(i+1)+'</a>').join(' ');
}
function showBill(index) { location.href='/Eversource/Secure/BillView.aspx?BillView=synthetic'; }
render(1);
</script></body>
"""


def history(page):
    page.context.route(site.BILLING_URL, lambda r: r.fulfill(
        body=HISTORY, content_type="text/html"))
    page.context.route("**/BillView.aspx?*", lambda r: r.fulfill(
        body='<iframe id="main_printPdf" src="/Eversource/Secure/__ImageGrabber.axd?synthetic=1&amp;format=PDFINLINE"></iframe>',
        content_type="text/html"))
    page.goto(site.BILLING_URL)


def app(tmp_path, args=()):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"owner": "Example", "output_dir": str(tmp_path / "out"),
                                 "profile_dir": str(tmp_path / "profile"),
                                 "delay_min_seconds": 0, "delay_max_seconds": 0}))
    return engine.App(engine.build_parser().parse_args(["--config", str(config), *args]))


def listed(page, account="700001", day="2031-02-14"):
    site._apply(page, account, day, day)
    return site._rows(page, account, Counter())[0]


def test_windows_are_bounded_contiguous_and_retention_limited():
    windows = list(site.date_windows("2020-01-01", "2032-02-20", today=date(2032, 2, 20)))
    assert windows[0][0] == "2029-02-20"
    assert windows[-1][1] == "2032-02-20"
    for (first, last), following in zip(windows, windows[1:]):
        assert (date.fromisoformat(last) - date.fromisoformat(first)).days <= 365
        assert date.fromisoformat(last) + timedelta(days=1) == date.fromisoformat(following[0])
    assert list(site.date_windows("2033-01-01", today=date(2032, 2, 20))) == []
    assert list(site.date_windows("2031-02-14", "2031-02-14", date(2032, 2, 20))) == [
        ("2031-02-14", "2031-02-14")]


def test_every_account_page_and_same_day_bill_is_preserved(page, monkeypatch):
    history(page)
    real_windows = site.date_windows
    monkeypatch.setattr(site, "date_windows", lambda start, end: real_windows(
        start, end, today=date(2032, 2, 20)))
    docs = site.collect_download_docs(page, start="2031-01-01", end="2031-12-31")
    assert len(docs) == 5
    assert len({d.document_id for d in docs}) == 5
    assert {d.account for d in docs} == {"700001", "700002"}
    assert sum(d.date_text == "2031-02-14" for d in docs) == 4
    assert page.evaluate("window.unsafe") is None


def test_period_filter_skips_unwanted_windows(page, monkeypatch):
    history(page)
    monkeypatch.setattr(site, "date_windows", lambda *_: iter([
        ("2030-01-01", "2030-12-31"), ("2031-01-01", "2031-12-31")]))
    docs = site.collect_download_docs(page, keep=lambda year: year == "2031")
    assert len(docs) == 5


def test_empty_requires_filters_and_rendered_history(page):
    history(page)
    site._apply(page, "700003", "2031-01-01", "2031-12-31")
    assert site._rows(page, "700003", Counter()) == []
    page.locator("#empty").evaluate("el => el.remove()")
    with pytest.raises(RuntimeError, match="not rendered"):
        site._ready(page)


def test_real_download_path_uses_strict_delivery(page, monkeypatch, tmp_path):
    history(page)
    raw = listed(page)
    application = app(tmp_path)
    application._record_rawdoc(raw, site.BILLING_URL)
    doc = engine.Document.from_dict(next(iter(application.discovery.data.values())))
    seen = []

    def get(client, url, guard):
        assert guard(url)
        seen.append(url)
        return SimpleNamespace(ok=True, body=lambda: text_pdf([
            "Synthetic gas bill issued 2031-02-14, total $12.34",
            "This is generated test content, not a customer's bill."]),
                               headers={}, dispose=lambda: None)

    monkeypatch.setattr(site.redirects, "get", get)
    spy = StrictDelivery().install(monkeypatch)
    application.download_one(page, doc, "synthetic.pdf")
    assert seen and spy.calls[0].name == "place"
    assert spy.calls[0].arguments["expect"].date == "2031-02-14"
    assert spy.calls[0].arguments["expect"].total == "$12.34"
    assert spy.calls[0].arguments["rivals"]
    assert application.progress.get(doc.key)["state"] == State.COMPLETED.value
    assert application._already_done(doc)
    Path(doc.pdf_path).unlink()
    assert application._already_done(doc)
    application.args.redownload = True
    assert not application._already_done(doc)


@pytest.mark.parametrize("data", [b"", b"<html>Sign in</html>", b"not a pdf"])
def test_invalid_bytes_never_become_a_successful_delivery(page, tmp_path, monkeypatch, data):
    history(page)
    raw = listed(page)
    doc = engine.Document(date=raw.date_text, account=raw.account,
                          document_id=raw.document_id, title=raw.title)
    monkeypatch.setattr(site.redirects, "get", lambda *a: SimpleNamespace(
        ok=True, body=lambda: data, headers={}, dispose=lambda: None))
    out = tmp_path / "bad.pdf"
    assert not site.download_document(page, doc, out)
    assert not out.exists()
    assert not list(tmp_path.glob("*.part"))


def test_pdf_identity_refuses_another_issue_date(tmp_path):
    out = tmp_path / "wrong.pdf"
    result = delivery.place(text_pdf(["Synthetic gas statement for 2031-02-14",
                                      "This is generated test content, not a customer's bill."]),
                            out, expect=site.Identity(date="2031-04-19"))
    assert not result.ok and not out.exists()


def test_actual_download_rejects_a_same_day_neighbor_bill(page, tmp_path, monkeypatch):
    history(page)
    raw = listed(page)
    doc = engine.Document(date=raw.date_text, account=raw.account,
                          document_id=raw.document_id, title=raw.title)
    monkeypatch.setattr(site.redirects, "get", lambda *a: SimpleNamespace(
        ok=True, body=lambda: text_pdf([
            "Synthetic gas bill issued 2031-02-14, total $56.78",
            "Generated test content only, representing a neighboring bill."]),
        headers={}, dispose=lambda: None))
    out = tmp_path / "wrong-neighbor.pdf"
    assert not site.download_document(page, doc, out)
    assert not out.exists()


def test_actual_download_checks_issue_dates_apart_from_same_day_amounts(page, tmp_path, monkeypatch):
    history(page)
    site._apply(page, "700001", "2031-01-01", "2031-12-31")
    known, counts = [], Counter()
    for _ in site._pages(page):
        known.extend(site._rows(page, "700001", counts))
    raw = known[0]
    doc = engine.Document(date=raw.date_text, account=raw.account,
                          document_id=raw.document_id, title=raw.title)
    monkeypatch.setattr(site.redirects, "get", lambda *a: SimpleNamespace(
        ok=True, body=lambda: text_pdf([
            "Synthetic gas bill issued 2031-03-18, previous balance $12.34",
            "Generated test content only, representing a different issue date."]),
        headers={}, dispose=lambda: None))
    out = tmp_path / "wrong-day.pdf"
    assert not site.download_document(page, doc, out, known_docs=known)
    assert not out.exists()


@pytest.mark.parametrize("label", [
    "Pay", "View bill | Pay", "Autopay", "Enroll", "Budget Billing",
    "Edit scheduled payment", "Cancel scheduled payment", "Account settings",
    "Submit meter readings", "View Bill | Save Changes"])
def test_utility_controls_are_never_safe(label):
    assert not site.is_safe_control(label)


def test_safe_labels_and_hosts():
    assert site.is_safe_control("VIEW")
    assert site.is_safe_control("Credit Card Statement")
    for url in ("javascript:alert(1)", "http://secure8.i-doxs.net/a",
                "https://secure8.i-doxs.net.evil.test/a", "https://secure8.i-doxs.net:bad/a",
                "blob:https://secure8.i-doxs.net/synthetic", "//secure8.i-doxs.net/a"):
        assert not site.is_safe_url(url)
    assert not site.is_document_url(site.BILLING_URL)


def test_redirect_outside_document_allowlist_is_not_followed():
    asked = []

    def get(url, **kw):
        asked.append((url, kw))
        return SimpleNamespace(status=302, headers={"location": "https://evil.test/bill.pdf"},
                               url=url)

    response = redirects.get(SimpleNamespace(get=get),
                             site.BASE + "/Eversource/Secure/__ImageGrabber.axd?synthetic=1",
                             site.is_document_url)
    assert response.status == 302 and len(asked) == 1
    assert asked[0][1]["max_redirects"] == 0


def test_dangerous_view_label_and_filter_are_refused(page):
    history(page)
    page.locator(site.FALLBACK["doc_link"]).first.evaluate(
        "el => el.setAttribute('aria-label','Pay Bill')")
    raw = listed(page)
    page.locator(site.FALLBACK["doc_link"]).first.evaluate(
        "el => el.setAttribute('aria-label','Pay Bill')")
    with pytest.raises(RuntimeError, match="labels were refused"):
        site._press(page, page.locator(site.FALLBACK["doc_link"]).first,
                    site.FALLBACK["doc_link"], site.is_safe_control, "bill")
    page.locator("#main_txtStartDate").evaluate(
        "el => el.setAttribute('aria-label','Submit meter readings')")
    with pytest.raises(RuntimeError, match="refused"):
        site._apply(page, raw.account, raw.date_text, raw.date_text)


def test_review_failure_and_interrupt_are_retryable(tmp_path, monkeypatch):
    application = app(tmp_path)
    doc = engine.Document(date="2031-02-14", account="700001", document_id="synthetic", category="Statement")
    for state in (State.FAILED, State.NEEDS_MANUAL_REVIEW):
        application.progress.update(doc.key, {"state": state.value, "downloaded_ok": True})
        assert not application._already_done(doc)
    monkeypatch.setattr(engine, "App", lambda args: application)
    monkeypatch.setattr(application, "cmd_pilot", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert engine.main(["--pilot"]) == 130


def test_session_expiration_stops_without_pressing(page):
    history(page)
    page.locator("body").evaluate("el => el.innerHTML = '<input type=password>'")
    with pytest.raises(pressing.Stop):
        site._session(page)


def test_pagination_cannot_repeat_or_hide_a_continuation(page):
    history(page)
    site._apply(page, "700001", "2031-01-01", "2031-12-31")
    page.locator(site.FALLBACK["pager"]).first.evaluate(
        "el => el.href = \"javascript:void('dpAccountHistory')\"")
    pages = site._pages(page)
    next(pages)
    with pytest.raises(RuntimeError, match="pagination repeated"):
        next(pages)
    page.locator("#main_dpAccountHistory").evaluate(
        """el => el.innerHTML =
        '<span>1</span><a href="javascript:void(\\'dpAccountHistory\\')">Next</a>'""")
    pages = site._pages(page)
    next(pages)
    with pytest.raises(RuntimeError, match="hidden later pages"):
        next(pages)


def test_filters_that_do_not_stick_are_not_an_empty_history(page):
    history(page)
    page.locator("#main_lnkApply").evaluate(
        """el => el.href = "javascript:document.querySelector('#main_ddlTypeFilter').value='Payments';render(1)" """)
    with pytest.raises(RuntimeError, match="filters changed"):
        site._apply(page, "700001", "2031-01-01", "2031-12-31")
