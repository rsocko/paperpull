"""Synthetic same-origin SPA/popup/blob fixtures, never a live sign-in."""
import base64
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from paperpull_core import pressing, tabs
from paperpull_core.models import State
from paperpull_core.testkit import StrictDelivery, text_pdf

import nationalgrid_docs as engine
import nationalgrid_site as site


HISTORY = """
<body><main>
<h1>Gas Bill History</h1>
<section>Current bill <span>Issue date: March 18, 2031</span>
<span>Due date: April 4, 2031</span>
<button data-testid="view-current-bill-pdf-button"
 onclick="view('2031-03-18','current')">View current bill</button></section>
<table><tr><td>February 14, 2031</td><td>
<button data-testid="view-previous-bill-pdf-button-2031-02-14"
 onclick="view('2031-02-14','past')">View Bill</button></td></tr>
<tr><td>February 27, 2031</td><td>
<button data-testid="view-previous-bill-pdf-button-2031-02-27"
 onclick="view('2031-02-27','past')">View Bill</button></td></tr></table>
<p>We display up to 2 years of bill history. For prior bills, please contact Customer Service.</p>
<button onclick="window.unsafe = true">Pay Bill</button>
</main><script>
sessionStorage.setItem('synthetic-sign-in','yes');
function view(date, kind) {
 URL.createObjectURL(new Blob([], {type:'application/pdf'}));
 const q = new URLSearchParams({accountLink:'synthetic-account',date:date,billType:kind});
 window.open('/bill-history/viewbillpdf?' + q.toString(), '_blank');
}
</script></body>
"""


def history(page, data=None, mismatch=False):
    observed = []
    page.context.route(site.BILLING_URL + "?accountLink=synthetic-account",
                       lambda r: r.fulfill(body=HISTORY, content_type="text/html"))
    page.context.route("**/viewer.html?*", lambda r: r.fulfill(
        body="<body>Synthetic PDF viewer</body>", content_type="text/html"))

    def view(route):
        query = parse_qs(urlsplit(route.request.url).query)
        observed.append(query)
        day = query["date"][0]
        content = data if data is not None else text_pdf([
            f"Synthetic National Grid gas bill issued {day}",
            "Generated test document only. No customer information."])
        encoded = base64.b64encode(content).decode()
        body = """<body><script>
        if (window === window.top || parent.sessionStorage.getItem('synthetic-sign-in') !== 'yes') {
          document.body.innerText = 'Sign in required';
        } else {
          const bytes = Uint8Array.from(atob('%s'), c => c.charCodeAt(0));
          const blob = URL.createObjectURL(new Blob([bytes], {type:'application/pdf'}));
          const child = document.createElement('iframe');
          child.src = '/_uwp2/pdf/resource/ng_pdfjs3/web/viewer.html?file=' + encodeURIComponent(blob);
          document.body.appendChild(child);
        }
        </script></body>""" % encoded
        route.fulfill(body=body, content_type="text/html")

    page.context.route("**/bill-history/viewbillpdf?*", view)
    page.goto(site.BILLING_URL + "?accountLink=synthetic-account")
    if mismatch:
        page.locator(site.PREVIOUS).first.evaluate(
            "el => el.setAttribute('data-testid','view-previous-bill-pdf-button-2031-01-01')")
    return observed


def app(tmp_path, args=()):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"owner": "Example", "output_dir": str(tmp_path / "out"),
                                 "profile_dir": str(tmp_path / "profile"),
                                 "delay_min_seconds": 0, "delay_max_seconds": 0}))
    return engine.App(engine.build_parser().parse_args(["--config", str(config), *args]))


def document(raw):
    return engine.Document(date=raw.date_text, account=raw.account, title=raw.title,
                           document_id=raw.document_id, category="Statement")


def test_current_and_previous_are_listed_by_issue_day_not_month(page, capsys):
    history(page)
    docs = site.collect_download_docs(page)
    assert [d.date_text for d in docs] == ["2031-03-18", "2031-02-14", "2031-02-27"]
    assert len({d.document_id for d in docs}) == 3
    assert "2 years" in capsys.readouterr().out
    assert all(d.account != "synthetic-account" and not d.href for d in docs)
    assert [d.date_text for d in site.collect_download_docs(
        page, start="2031-02-20", end="2031-03-01")] == ["2031-02-27"]
    assert site.collect_download_docs(page, keep=lambda y: y == "2020") == []


@pytest.mark.parametrize("index,kind", [(0, "current"), (1, "past")])
def test_real_download_path_uses_strict_delivery(page, monkeypatch, tmp_path, index, kind):
    observed = history(page)
    raw = site.collect_download_docs(page)[index]
    application = app(tmp_path)
    application._record_rawdoc(raw, site.BILLING_URL)
    doc = engine.Document.from_dict(next(iter(application.discovery.data.values())))
    spy = StrictDelivery().install(monkeypatch)
    application.download_one(page, doc, "synthetic.pdf")
    assert spy.calls[0].name == "place"
    assert spy.calls[0].arguments["data"].startswith(b"%PDF-")
    assert spy.calls[0].arguments["expect"].date == raw.date_text
    assert {q["billType"][0] for q in observed} == {kind}
    assert application.progress.get(doc.key)["state"] == State.COMPLETED.value
    assert application._already_done(doc)
    Path(doc.pdf_path).unlink()
    assert application._already_done(doc)
    application.args.redownload = True
    assert not application._already_done(doc)
    assert len(page.context.pages) == 1
    assert not page.locator("iframe[id^='paperpull-bill-']").count()
    assert page.evaluate("window.unsafe") is None


def test_actual_current_and_past_blob_bytes_are_distinct(page, tmp_path):
    history(page)
    docs = site.collect_download_docs(page)
    paths = [tmp_path / f"bill-{i}.pdf" for i in range(2)]
    for raw, out in zip(docs, paths):
        assert site.download_document(page, document(raw), out)
    assert all(p.read_bytes().startswith(b"%PDF-") for p in paths)
    assert paths[0].read_bytes() != paths[1].read_bytes()


@pytest.mark.parametrize("data", [b"", b"<html>Sign in</html>", b"not a pdf"])
def test_empty_capability_or_invalid_blob_never_succeeds(page, tmp_path, data):
    history(page, data=data)
    doc = document(site.collect_download_docs(page)[0])
    out = tmp_path / "bad.pdf"
    with pytest.raises(Exception, match="did not return a PDF"):
        site.download_document(page, doc, out)
    assert not out.exists()
    assert len(page.context.pages) == 1
    assert not page.locator("iframe[id^='paperpull-bill-']").count()


def test_rendered_empty_list_is_distinct_from_blank_or_signin(page):
    history(page)
    page.locator("main").evaluate("el => el.innerHTML = '<h1>Bill History</h1><p>No bills available</p>'")
    assert site.collect_download_docs(page) == []
    page.locator("main").evaluate("el => el.innerHTML = '<input type=password>'")
    with pytest.raises(pressing.Stop):
        site.collect_download_docs(page)


def test_current_bill_alone_does_not_finish_a_still_loading_history(page):
    history(page)
    page.evaluate("""() => {
      const main = document.querySelector('main');
      const table = main.querySelector('table');
      const footer = main.querySelector('p');
      table.remove(); footer.remove();
      setTimeout(() => { main.appendChild(table); main.appendChild(footer); }, 200);
    }""")
    assert len(site.collect_download_docs(page)) == 3


def test_visible_issue_date_must_match_test_id(page):
    history(page, mismatch=True)
    with pytest.raises(RuntimeError, match="disagrees"):
        site.collect_download_docs(page)


def test_current_becomes_past_without_changing_document_key(page):
    history(page)
    before = site.collect_download_docs(page)[0]
    page.locator(site.CURRENT).evaluate(
        "el => {el.dataset.testid = 'view-previous-bill-pdf-button-2031-03-18'; el.innerText = 'View Bill';}")
    after = site.collect_download_docs(page)[0]
    assert before.document_id == after.document_id
    assert document(before).key == document(after).key


def test_unknown_pagination_is_not_reported_as_complete(page):
    history(page)
    page.locator("main").evaluate("el => el.insertAdjacentHTML('beforeend','<button>Load more bills</button>')")
    with pytest.raises(RuntimeError, match="additional pages"):
        site.collect_download_docs(page)


def test_session_and_selected_account_cannot_be_substituted(page, tmp_path):
    history(page)
    doc = document(site.collect_download_docs(page)[0])
    doc.account = "different-synthetic-account"
    with pytest.raises(RuntimeError, match="account or listed bill changed"):
        site.download_document(page, doc, tmp_path / "bad.pdf")
    page.locator("body").evaluate("el => el.innerHTML = 'Your session has expired'")
    with pytest.raises(pressing.Stop):
        site._session(page)


def test_document_routes_and_blob_origins_are_strict():
    route = site.BASE + "/bill-history/viewbillpdf?accountLink=synthetic&date=2031-02-14&billType=past"
    assert site._view_route(route, "synthetic", "2031-02-14", "past")
    for bad in (route.replace("past", "current"), route.replace("2031-02-14", "2031-02-15"),
                route.replace("accountLink=synthetic", "accountLink=other"),
                route.replace("myaccount.nationalgrid.com", "evil.test")):
        assert not site._view_route(bad, "synthetic", "2031-02-14", "past")
    viewer = site.BASE + "/_uwp2/pdf/resource/ng_pdfjs3/web/viewer.html?file="
    assert site._blob_from_viewer(viewer + "blob:" + site.BASE + "/synthetic")
    assert not site._blob_from_viewer(viewer + "blob:https://evil.test/synthetic")
    assert not site._blob_from_viewer(viewer + "https://evil.test/statement.pdf")
    assert not site._blob_from_viewer(viewer.replace("viewer.html", "other.html") +
                                      "blob:" + site.BASE + "/synthetic")


@pytest.mark.parametrize("label", [
    "Pay", "View Bill | Pay", "Autopay", "Enroll", "Budget Billing",
    "Edit scheduled payment", "Cancel scheduled payment", "Account settings",
    "Submit meter readings", "View Bill | Save Changes"])
def test_utility_controls_are_never_safe(label):
    assert not site.is_safe_control(label)


def test_own_button_label_is_guarded_before_any_popup(page, tmp_path):
    history(page)
    doc = document(site.collect_download_docs(page)[0])
    page.locator(site.CURRENT).evaluate("el => el.setAttribute('aria-label','Pay Bill')")
    with pytest.raises(RuntimeError, match="labels were refused"):
        site.download_document(page, doc, tmp_path / "bad.pdf")
    assert len(page.context.pages) == 1
    assert site.is_safe_control("Credit Card Statement")


def test_reviews_failures_and_interrupt_remain_retryable(tmp_path, monkeypatch):
    application = app(tmp_path)
    doc = engine.Document(date="2031-02-14", account="synthetic", document_id="synthetic", category="Statement")
    for state in (State.FAILED, State.NEEDS_MANUAL_REVIEW):
        application.progress.update(doc.key, {"state": state.value, "downloaded_ok": True})
        assert not application._already_done(doc)
    monkeypatch.setattr(engine, "App", lambda args: application)
    monkeypatch.setattr(application, "cmd_pilot", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert engine.main(["--pilot"]) == 130


def test_tab_bound_resume_never_uses_an_unrelated_tab(tmp_path):
    application = app(tmp_path)
    unrelated = type("Tab", (), {"url": "https://evil.test/", "is_closed": lambda self: False})()
    application.page = lambda: unrelated
    with pytest.raises(SystemExit):
        tabs.on_its_site(application, site.is_safe_url, "National Grid")


def test_outgoing_route_mismatch_closes_only_its_popup(page, tmp_path):
    history(page)
    doc = document(site.collect_download_docs(page)[0])
    unrelated = page.context.new_page()
    unrelated.goto("https://unrelated.test/")
    page.locator(site.CURRENT).evaluate(
        """el => el.setAttribute('onclick', "view('2031-02-14','past')")""")
    with pytest.raises(RuntimeError, match="route disagrees"):
        site.download_document(page, doc, tmp_path / "bad.pdf")
    assert set(page.context.pages) == {page, unrelated}
    assert not page.locator("iframe[id^='paperpull-bill-']").count()
    assert page.evaluate("sessionStorage.getItem('synthetic-sign-in')") == "yes"


def test_interrupted_blob_capture_removes_frame_and_popup(page, tmp_path, monkeypatch):
    history(page)
    doc = document(site.collect_download_docs(page)[0])

    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(site.intake, "evaluate", interrupt)
    with pytest.raises(KeyboardInterrupt):
        site.download_document(page, doc, tmp_path / "interrupted.pdf")
    assert len(page.context.pages) == 1
    assert not page.locator("iframe[id^='paperpull-bill-']").count()
    assert not list(tmp_path.iterdir())
