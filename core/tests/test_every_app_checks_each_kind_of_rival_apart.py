"""Every app checks a document against each kind of rival apart.

identity.distinguish asks which of several listed documents a saved PDF
names best, and it counts only the facts no rival shares. Handed one pool
holding two kinds of rival, that rule takes everything away. Another
account's statement of the same day differs by its account and shares the
day. The account's own statement of another day differs by its day and
shares the account. Pooled, the day goes because the first shares it and
the account goes because the second does, so nothing of the statement is
left to count. A wrong statement is then filed unchecked, or verified on
some fact no rival carries at all, which says nothing about which
statement it is. Bank of America's first build did this and a fresh
review caught it (#121). Navy Federal had done it since the check was
built, and on the sixteen of its owner's sixty four statements that share
a day with the other account's, the check was never made.

So an app whose identities carry more than one fact checks against each
kind apart, as Bank of America's download_one does. It hands delivery the
documents of the same day, then asks identity.distinguish again against
the documents either side on other days. Navy Federal, Costco and PayPal
hand delivery those either side when there are none of the same day, so
no check of theirs is the plain check of the day where a rival exists,
since that refuses a document for not printing its day in a form the
check knows, and both of the first two destroy what is refused.

An app is found here by what it does, handing rivals to a check, never by
its name. One whose identities carry a single fact cannot pool two kinds,
since every rival differs from it by that fact or by nothing, and that is
read from its code. Every other app's pools are built here by its own
code, from rows of two accounts billed on shared days or their like, and
each pool handed to one check must keep a fact that tells every rival in
it apart. A fact a rival does not carry at all does not count, since
nothing compared it. It needs no browser and no account.
"""
import ast
import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
APPS = REPO / "apps"
sys.path.insert(0, str(REPO / "core"))

from paperpull_core.identity import (  # noqa: E402
    REFUSED, UNCHECKED, VERIFIED, Identity, distinguish)

FOLDERS = sorted(d for d in APPS.iterdir()
                 if d.is_dir() and not d.name.startswith((".", "_")))

# The facts distinguish counts. `kind` is recorded and never counted.
STRONG = ("date", "start", "period", "total", "number", "extra", "label")


def told_apart_by(expect, pool) -> tuple:
    """The facts of `expect` that every rival in `pool` carries with another
    value, the ones a check against the whole pool still counts.

    A fact is shared with a rival when any way of printing it is among the
    rival's, whatever the rival calls it, which is how distinguish takes it
    away. A rival that differs by nothing it carries, a twin of the same
    day and account or one carrying none of these facts, is left out, since
    no pool could tell it apart and leaving it in would say every pool
    mixes. With no rival left, every fact of `expect` counts, as a plain
    check of it would."""
    mine = expect.marks()
    kept = None
    for rival in pool or ():
        theirs = rival.marks()
        printed = {str(v).lower() for variants in theirs.values() for v in variants}
        differs = {name for name, variants in mine.items()
                   if name in theirs and not {str(v).lower() for v in variants} & printed}
        if not differs:
            continue
        kept = differs if kept is None else kept & differs
    return tuple(sorted(mine if kept is None else kept))


# -- the mechanism, before asking which apps meet it ---------------------------------

DAY, BEFORE, AFTER = "2026-08-15", "2026-07-15", "2026-09-15"
CHECKING, CARD = "Everyday Checking", "Visa Signature Rewards"


def statement(day: str, account: str, *more: str) -> str:
    """A statement's text, naming its day and account as a real one does."""
    return "\n".join(["Navy Federal Credit Union", "Statement of Account",
                      "Statement closing date %s/%s/%s" % (day[5:7], day[8:10], day[:4]),
                      account, "Beginning balance and ending balance", *more])


ASKED = Identity(date=DAY, label=CHECKING)
SAME_DAY = [Identity(date=DAY, label=CARD)]
EITHER_SIDE = [Identity(date=BEFORE, label=CHECKING), Identity(date=AFTER, label=CHECKING)]
# Both accounts billed on each day, as Navy Federal bills them.
ONE_POOL = SAME_DAY + EITHER_SIDE + [Identity(date=BEFORE, label=CARD),
                                     Identity(date=AFTER, label=CARD)]


def test_one_pool_of_both_kinds_leaves_the_statement_nothing_to_count():
    """The right statement, naming its own day and account, is placed by
    nothing, since the card's statement shares the day and the account's
    own months share the account."""
    assert told_apart_by(ASKED, ONE_POOL) == ()
    assert distinguish(None, ASKED, ONE_POOL, text=statement(DAY, CHECKING)).outcome == UNCHECKED


def test_one_pool_lets_the_accounts_other_month_through_unchecked():
    """July's checking statement came back for August's. Pooled, July's day
    is the card's July statement's too and its account is August's, so July
    keeps nothing of its own either, and nothing refuses it."""
    july = statement(BEFORE, CHECKING)
    assert distinguish(None, ASKED, ONE_POOL, text=july).outcome == UNCHECKED
    # Apart, the account tells it from the card's statement and its day
    # from its own account's either side, which refuses it.
    assert distinguish(None, ASKED, SAME_DAY, text=july).outcome == VERIFIED
    assert distinguish(None, ASKED, EITHER_SIDE, text=july).outcome == REFUSED


def test_each_kind_apart_tells_the_right_statement_from_both():
    right = statement(DAY, CHECKING)
    assert distinguish(None, ASKED, SAME_DAY, text=right).outcome == VERIFIED
    assert distinguish(None, ASKED, EITHER_SIDE, text=right).outcome == VERIFIED
    card = statement(DAY, CARD)
    assert distinguish(None, ASKED, SAME_DAY, text=card).outcome == REFUSED
    assert told_apart_by(ASKED, SAME_DAY) == ("label",)
    assert told_apart_by(ASKED, EITHER_SIDE) == ("date",)


def test_a_fact_no_rival_carries_verifies_one_pool_with_nothing_checked():
    """A fact the rivals do not carry outlives the pool, and the wrong month
    is verified on it alone, with its day and account never compared. So
    such a fact does not count as telling the rivals apart."""
    asked = Identity(date=DAY, label=CHECKING, total="84.12")
    july = statement(BEFORE, CHECKING, "Dividend 84.12")
    assert distinguish(None, asked, ONE_POOL, text=july).outcome == VERIFIED
    assert told_apart_by(asked, ONE_POOL) == ()


def test_a_twin_and_an_empty_pool_say_nothing_about_mixing():
    twin = Identity(date=DAY, label=CHECKING)
    assert told_apart_by(ASKED, [twin] + EITHER_SIDE) == ("date",)
    assert told_apart_by(ASKED, ()) == ("date", "label")


# -- which apps hand rivals to a check, read from their code ------------------------

def _name(func) -> str:
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _trees(app: Path):
    for path in sorted(app.glob("*.py")):
        try:
            yield path, ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue


def _empty(node) -> bool:
    """A pool written as nothing, () or []."""
    return isinstance(node, (ast.Tuple, ast.List)) and not node.elts


def _what(call) -> str:
    """What a call does with rivals, or "" when it is not such a call."""
    name = _name(call.func)
    if name == "distinguish":
        given = [k.value for k in call.keywords if k.arg == "others"] + call.args[2:3]
        if given and not _empty(given[0]):
            return "distinguish"
    if name in ("deliver", "place", "render", "DocumentRequest"):
        for k in call.keywords:
            if k.arg == "rivals" and not _empty(k.value):
                return name
    if name == "rivals_for":
        return name
    return ""


def _calls(app: Path):
    """Every call in an app's own code, as (file, call, innermost function)."""
    for path, tree in _trees(app):
        found = []

        def visit(node, where):
            for child in ast.iter_child_nodes(node):
                inner = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else where
                if isinstance(child, ast.Call):
                    found.append((path.name, child, where))
                visit(child, inner)
        visit(tree, "")
        yield from found


def handovers(app: Path) -> list:
    """Every place an app's own code builds rivals or hands them to a check,
    as (file, line, innermost function, what). An app that puts its rivals
    on a DocumentRequest hands them over at each deliver it makes, whatever
    that call is given."""
    out = [(file, call.lineno, where, _what(call)) for file, call, where in _calls(app)
           if _what(call)]
    if any(h[3] == "DocumentRequest" for h in out):
        out += [(file, call.lineno, where, "deliver") for file, call, where in _calls(app)
                if _name(call.func) == "deliver" and not _what(call)]
    return out


def facts_of(app: Path) -> tuple:
    """(the facts every Identity the app builds carries between them, the
    lines of any whose facts cannot be read from the code)."""
    facts, unread = set(), []
    for path, tree in _trees(app):
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _name(node.func)
            if name == "Identity":
                if node.args or any(k.arg is None for k in node.keywords):
                    unread.append("%s:%d" % (path.name, node.lineno))
                facts |= {k.arg for k in node.keywords if k.arg in STRONG}
            elif name == "from_purchase":
                facts |= {"date", "total", "number"}
    return facts, unread


CHECKERS = [d for d in FOLDERS if handovers(d)]
IDS = [d.name for d in CHECKERS]


def one_fact(app: Path) -> bool:
    facts, unread = facts_of(app)
    return len(facts) <= 1 and not unread


def test_the_reader_finds_both_ways_of_handing_rivals_over():
    tree = ast.parse("from paperpull_core import delivery, identity\n"
                     "def take(out, expect, same, near):\n"
                     "    got = delivery.place(b'', out, expect=expect, rivals=same)\n"
                     "    identity.distinguish(out, expect, near)\n"
                     "    delivery.place(b'', out, expect=expect, rivals=())\n")
    found = [_what(n) for n in ast.walk(tree) if isinstance(n, ast.Call) and _what(n)]
    assert found == ["place", "distinguish"]
    nothing = ast.parse("identity.distinguish(out, expect, ())\n"
                        "identity.distinguish(out, expect, others=[])\n")
    assert not [n for n in ast.walk(nothing) if isinstance(n, ast.Call) and _what(n)]


def test_the_census_finds_every_app_that_checks_against_rivals():
    """The eleven found on 2026-10-25. One that stops being found has moved
    its check, and this says so rather than letting it leave quietly."""
    assert set(IDS) >= {"bilt", "bofa", "coinbase", "costco", "fairfaxwater", "navyfederal",
                        "paypal", "redcard", "spectrum", "tmobile", "tsp"}, IDS


# -- an app of one fact cannot mix -------------------------------------------------

def test_the_apps_of_one_fact_are_still_of_one_fact():
    """Each of these builds every Identity from the day alone, so a rival
    differs from a document by its day or by nothing, and no pool of its can
    hold two kinds. A new fact added to one moves it below, where its pools
    are built and read."""
    assert {d.name for d in CHECKERS if one_fact(d)} >= {
        "bilt", "fairfaxwater", "redcard", "spectrum", "tmobile", "tsp"}


# -- an app of more facts checks each kind apart -----------------------------------

def load(folder: Path, module: str):
    """The module, imported fresh, with no other app's modules standing in
    for its own. Every app has a storage.py and a site module."""
    for name, mod in list(sys.modules.items()):
        where = getattr(mod, "__file__", None) or ""
        if where and os.path.normcase(os.path.abspath(where)).startswith(
                os.path.normcase(str(APPS))):
            del sys.modules[name]
    sys.path.insert(0, str(folder))
    try:
        return importlib.import_module(module)
    finally:
        sys.path.pop(0)


# Two accounts, the second billed on two of the first's days.
STATEMENTS = [("2026-05-15", CHECKING), ("2026-06-03", CARD), ("2026-06-15", CHECKING),
              ("2026-07-15", CHECKING), ("2026-07-15", CARD), ("2026-08-15", CHECKING),
              ("2026-08-15", CARD), ("2026-09-03", CARD), ("2026-09-15", CHECKING)]


def bofa_pools():
    mod = load(APPS / "bofa", "bofa_docs")
    docs = [mod.Document(title="Statement", category="Statements", date=day, account=acct,
                         account_ref="ref-%d" % (acct == CARD), document_id="DOC%d" % n)
            for n, (day, acct) in enumerate(STATEMENTS)]
    app = SimpleNamespace(discovery=SimpleNamespace(data={d.key: d.to_dict() for d in docs}))
    app._identity = lambda doc: mod.App._identity(app, doc)
    return [(app._identity(d), mod.App._rivals(app, d)) for d in docs]


def navyfederal_pools():
    mod = load(APPS / "navyfederal", "navyfederal_docs")
    docs = [mod.Document(title="Statement", category="Statement", summary="%s Statement" % acct,
                         date=day, account=acct, document_id="doc-%d" % n)
            for n, (day, acct) in enumerate(sorted(STATEMENTS, reverse=True))]
    return [(mod.site.identity_for(d), mod.App._rivals(docs, d)) for d in docs]


def costco_pools():
    mod = load(APPS / "costco", "costco_receipts")
    from paperpull_core.models import Purchase
    # A warehouse visit for 60.00 beside an online order of the same day,
    # and a visit for 60.00 on another day among the rows either side.
    rows = [("2026-08-20", "wh-20260820-7710", "77.10"), ("2026-08-15", "1100000001", "143.21"),
            ("2026-08-15", "wh-20260815-6000", "60.00"), ("2026-08-09", "wh-20260809-6000", "60.00"),
            ("2026-07-30", "wh-20260730-1234", "12.34")]
    purchases = [Purchase(purchase_date=d, order_number=n, total=t) for d, n, t in rows]
    app = SimpleNamespace(_rows=[mod.site.identity_for(p) for p in purchases])
    out = []
    for i, p in enumerate(purchases):
        app._row_index = i
        out.append((mod.site.identity_for(p), mod.App._rivals(app)))
    return out


def paypal_pools():
    mod = load(APPS / "paypal", "paypal_docs")
    days = [("2031-06-01", "2031-06-30"), ("2031-07-01", "2031-07-31"),
            ("2031-08-01", "2031-08-31"), ("2031-07-01", "2031-08-31"),
            ("2031-09-01", "2031-09-30")]
    refs = [mod.site.Period(start=a, end=b) for a, b in days]
    app = SimpleNamespace(discovery=SimpleNamespace(
        data={"k%d" % n: {"href": r.href()} for n, r in enumerate(refs)}))
    return [(r.identity(), mod.App._business_rivals(app, r)) for r in refs]


def coinbase_pools():
    site = load(APPS / "coinbase", "coinbase_site")
    out = []
    for month in ("January 2026", "July 2026", "December 2026"):
        mine, rivals = site.statement_identity("Monthly Statement " + month)
        out.append((mine, (rivals,)))
    return out


def eversource_pools():
    site = load(APPS / "eversource", "eversource_site")
    rows = [
        site.RawDoc("Gas Bill", day, account, "synthetic-%d" % i, total=amount)
        for i, (day, account, amount) in enumerate([
            (DAY, "700001", "12.34"), (DAY, "700001", "56.78"),
            (BEFORE, "700001", "12.34"), (AFTER, "700001", "56.78"),
            (DAY, "700002", "12.34"), (BEFORE, "700002", "56.78")])]
    return [(site.identity_for(row), site.rival_pools(row, rows)) for row in rows]


# How each app of more than one fact builds the pools it hands to its checks,
# by its own code. Each answers (identity, the pools it checks apart) for
# every row of a list where the two kinds meet.
POOLS = {"bofa": bofa_pools, "navyfederal": navyfederal_pools, "costco": costco_pools,
         "paypal": paypal_pools, "coinbase": coinbase_pools, "eversource": eversource_pools}

MANY = [d for d in CHECKERS if not one_fact(d)]


@pytest.mark.parametrize("app", MANY, ids=[d.name for d in MANY])
def test_an_app_of_more_than_one_fact_has_its_pools_built_here(app):
    assert app.name in POOLS, (
        "%s checks against rivals with identities of more than one fact (%s), and "
        "nothing here builds its pools to see whether one mixes two kinds" % (
            app.name, ", ".join(sorted(facts_of(app)[0]))))


@pytest.mark.parametrize("name", sorted(POOLS))
def test_every_pool_handed_to_one_check_tells_each_rival_apart(name):
    found = POOLS[name]()
    assert found, "%s built no pools" % name
    for expect, pools in found:
        for pool in pools:
            assert told_apart_by(expect, pool), (
                "%s hands one check a pool whose rivals differ from a document by "
                "different facts, so every fact of it is shared by one of them and "
                "nothing is left to check it by" % name)


@pytest.mark.parametrize("name", sorted(POOLS))
def test_the_two_kinds_meet_in_each_list_built_here(name):
    """Otherwise the test above would pass on a list that never mixes. Each
    list here holds a row whose pools, taken as one, would leave it nothing
    to be checked by."""
    found = POOLS[name]()
    if name == "coinbase":
        # Its rivals are the months either side and nothing else, each
        # differing by all three of its facts, so there is no second kind.
        assert all(len(pool) == 2 for _, pools in found for pool in pools)
        return
    together = [(e, [r for pool in pools for r in pool]) for e, pools in found]
    assert any(not told_apart_by(e, rivals) for e, rivals in together), (
        "no row of %s's list here holds both kinds, so its pools prove nothing" % name)


@pytest.mark.parametrize("app", MANY, ids=[d.name for d in MANY])
def test_an_app_that_hands_delivery_one_kind_checks_again_against_the_other(app):
    """delivery checks against the rivals it is handed, the same day's, and
    the function that handed them checks the saved file again against the
    others, as Bank of America's download_one does."""
    found = handovers(app)
    for file, line, where, what in found:
        if what not in ("deliver", "place", "render"):
            continue
        again = [h for h in found if h[0] == file and h[2] == where
                 and h[3] == "distinguish" and h[1] > line]
        assert again, (
            "%s/%s line %d hands delivery its rivals in %s and nothing there checks "
            "the saved file again against another kind" % (app.name, file, line, where))


@pytest.mark.parametrize("app", MANY, ids=[d.name for d in MANY])
def test_an_app_of_more_facts_hands_delivery_one_group_whole(app):
    """Its pools are built apart above, and joined again where they are
    handed over, two groups added together or the whole neighborhood
    identity.rivals_for answers, they would be one pool again."""
    for file, call, where in _calls(app):
        if _name(call.func) not in ("deliver", "place", "render", "DocumentRequest"):
            continue
        for k in call.keywords:
            if k.arg == "rivals":
                joined = isinstance(k.value, ast.BinOp) or (
                    isinstance(k.value, ast.Call) and _name(k.value.func) == "rivals_for")
                assert not joined, "%s/%s line %d hands %s one pool of %s" % (
                    app.name, file, call.lineno, _name(call.func), ast.unparse(k.value))
