"""Stage 1 attempts that run offline: A1-1, A1-2, A1-5 plus the port of the rails.

Fakes stand in for the site, SIG, the chain and the model. Every assertion is a
behaviour, never a setting.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from stoby import answer as answer_mod  # noqa: E402
from stoby.answer import CANT_VERIFY, Pipeline  # noqa: E402
from stoby.leads import Leads, extract_email  # noqa: E402
from stoby.ledger import SpendLedger, cost_usd  # noqa: E402
from stoby.llm import Draft, load_prompt  # noqa: E402
from stoby.rails import ComplianceRails, drop_raise_sentences  # noqa: E402
from stoby.sources.chain import ChainReader, contracts_from_site  # noqa: E402
from stoby.sources.sig import SigClient, SigDown, scrub  # noqa: E402
from stoby.sources.site import SiteFacts, SiteSource, SiteUnavailable  # noqa: E402
from stoby.telegram import RateLimit, split_for_telegram  # noqa: E402
from stoby.verify import ungrounded  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (Path(__file__).parent / "fixtures" / "llms-full-2026-09-27.txt").read_text()
LIVE = "0xe0c0F44A84CC4a60206360006ebA237a5e8fC2dd"


# --- fakes ----------------------------------------------------------------

def site_client(text: str = FIXTURE, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=text if status == 200 else "", headers={"etag": "x"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def sig_client(verdict: str = "no_contradiction_detected", status: int = 200, correction=None,
               calls: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, text="down")
        body = json.loads(request.content)
        if calls is not None:
            calls.append([c["params"]["name"] for c in body])
        out = []
        for c in body:
            name = c["params"]["name"]
            if name == "stobox_answer_context":
                res = {"query": c["params"]["arguments"]["query"], "entities": [
                    {"id": "stobox", "description": "Founded in 2018. Founder and CEO: Gene Deyev."}]}
            else:
                res = {"verdict": verdict, "issues": [], "correction": correction}
            out.append({"jsonrpc": "2.0", "id": c["id"],
                        "result": {"content": [{"type": "text", "text": json.dumps(res)}]}})
        return httpx.Response(200, json=out)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class FakeModel:
    def __init__(self, texts: list[str]) -> None:
        self.texts, self.calls = list(texts), 0

    async def draft(self, sources, question, correction=None) -> Draft:
        self.calls += 1
        text = self.texts.pop(0) if self.texts else "ok"
        return Draft(text=text, usd=0.01, input_tokens=100, output_tokens=50,
                     cache_read=0, stop_reason="end_turn")


def pipeline(tmp_path, model: FakeModel, sig_kwargs=None, site_text=FIXTURE):
    site = SiteSource("https://site.test/llms-full.txt", tmp_path, client=site_client(site_text))
    sig = SigClient("https://sig.test/mcp", client=sig_client(**(sig_kwargs or {})))
    return Pipeline(site, sig, model, SpendLedger(tmp_path, 5.0))


# --- A1-1: no facts in the bot ---------------------------------------------

def test_a1_1_no_fact_files_and_no_addresses_in_the_package():
    pkg = ROOT / "src" / "stoby"
    prompt = load_prompt()
    assert not re.search(r"0x[0-9a-fA-F]{40}", prompt)
    assert not re.search(r"\d", prompt), "the behaviour prompt carries no figures or dates"
    for f in pkg.rglob("*.py"):
        assert not re.search(r"0x[0-9a-fA-F]{40}", f.read_text()), f
        for needle in ("31 December 2026", "216,563,456", "$305M", "250,000,000"):
            assert needle not in f.read_text(), (f, needle)


# --- site -------------------------------------------------------------------

def test_site_parses_sections_and_addresses():
    facts = SiteFacts.parse(FIXTURE, time.time())
    assert "STBU, the token" in facts.sections
    assert LIVE.lower() in facts.addresses
    assert facts.where_to_buy and "does not sell STBU" in facts.where_to_buy
    live, legacy = contracts_from_site(facts)
    assert live == LIVE and set(legacy) == {"Ethereum", "BNB Chain", "Polygon", "Arbitrum"}


def test_site_without_stbu_section_is_unavailable():
    with pytest.raises(SiteUnavailable):
        SiteFacts.parse("# Stobox\n\n## Products\n- x", time.time())


async def test_site_uses_copy_within_stale_window_then_fails(tmp_path):
    src = SiteSource("https://site.test/x", tmp_path, cache_minutes=0, client=site_client())
    first = await src.facts()
    src.client = site_client(status=503)
    src._facts = None
    again = await src.facts(now=first.fetched_at + 3600)
    assert again.fetched_at == first.fetched_at
    src._facts = None
    with pytest.raises(SiteUnavailable):
        await src.facts(now=first.fetched_at + 25 * 3600)


# --- A1-2: a site edit reaches the answer with no commit -------------------

async def test_a1_2_site_edit_changes_answer_without_a_commit(tmp_path):
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout
    edited = FIXTURE.replace("Claims close 31 December 2026 at 23:59 UTC",
                             "Claims close 15 March 2027 at 23:59 UTC")
    seen = {}

    class EchoModel(FakeModel):
        async def draft(self, sources, question, correction=None):
            seen["sources"] = sources
            m = re.search(r"Claims close (\d+ \w+ \d{4})", sources)
            return await super().draft(sources, question, correction) if not m else Draft(
                f"Claims close {m.group(1)} at 23:59 UTC.", 0.01, 1, 1, 0, "end_turn")

    p = pipeline(tmp_path, EchoModel([]), site_text=edited)
    reply = await p.answer("When do claims close?")
    assert "15 March 2027" in reply.text
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                          text=True).stdout == head


# --- A1-5: a source down means no model call -------------------------------

async def test_a1_5_sig_down_means_fixed_reply_and_zero_model_calls(tmp_path):
    model = FakeModel(["should never be used"])
    p = pipeline(tmp_path, model, sig_kwargs={"status": 503})
    reply = await p.answer("What is Stobox Intelligence?")
    assert reply.text == CANT_VERIFY and reply.outcome == "fixed_sig_down"
    assert model.calls == 0


async def test_a1_5_site_down_means_fixed_reply_and_zero_model_calls(tmp_path):
    model = FakeModel(["never"])
    p = pipeline(tmp_path, model, site_text="# nothing here")
    reply = await p.answer("What is Stobox?")
    assert reply.outcome == "fixed_site_down" and model.calls == 0


async def test_sig_circuit_breaker_and_unreachable_host(tmp_path):
    sig = SigClient("http://127.0.0.1:9/mcp", timeout_s=0.5)
    with pytest.raises(SigDown):
        await sig.context("x")
    with pytest.raises(SigDown):           # breaker open, no second connection attempt
        await sig.context("x")


def test_sig_query_is_scrubbed_before_it_leaves():
    out = scrub("my wallet 0x1111111111111111111111111111111111111111 mail ann@acme.com @ann +1 415 555 0100")
    assert "0x1111" not in out and "acme.com" not in out and "@ann" not in out and "555" not in out


async def test_sig_refuses_write_tools():
    sig = SigClient("https://sig.test/mcp", client=sig_client())
    with pytest.raises(ValueError):
        await sig._batch([("stobox_start_tokenization", {})])


# --- grounding and fact-check loop -----------------------------------------

def test_ungrounded_finds_invented_figures_and_addresses():
    src = f"Contract {LIVE}. Claims close 31 December 2026. Cap 250,000,000."
    assert ungrounded("Claims close 31 December 2026, cap 250,000,000.", src) == []
    bad = ungrounded("Claims close 15 January 2027; contract 0x" + "a" * 40, src)
    assert "2027" in bad and ("0x" + "a" * 40) in bad
    assert ungrounded("1:1, one pool, 2 tokens", src) == []


async def test_ungrounded_draft_is_regenerated_once_then_refused(tmp_path):
    model = FakeModel(["Claims close 15 January 2027.", "Claims close 15 February 2027."])
    p = pipeline(tmp_path, model)
    reply = await p.answer("When do claims close?")
    assert model.calls == 2 and reply.outcome == "fixed_unverified"
    assert reply.text == CANT_VERIFY


async def test_contradicted_draft_is_regenerated_with_the_correction(tmp_path):
    seen = []

    class M(FakeModel):
        async def draft(self, sources, question, correction=None):
            seen.append(correction)
            return await super().draft(sources, question, correction)

    model = M(["Stobox sells STBU.", "Stobox does not sell STBU."])
    p = pipeline(tmp_path, model, sig_kwargs={"verdict": "contradicted",
                                              "correction": "Stobox does not sell STBU."})
    reply = await p.answer("Does Stobox sell STBU?")
    assert seen[1] == "Stobox does not sell STBU."
    assert reply.outcome == "fixed_unverified"     # the fake SIG contradicts every draft


async def test_daily_cap_stops_the_model(tmp_path):
    model = FakeModel(["x"])
    p = pipeline(tmp_path, model)
    p.ledger.add(5.0)
    reply = await p.answer("What is Stobox?")
    assert reply.outcome == "fixed_cap" and model.calls == 0


async def test_buy_intent_is_answered_from_the_site_without_the_model(tmp_path):
    model = FakeModel(["never"])
    p = pipeline(tmp_path, model)
    reply = await p.answer("I want to buy STBU, walk me through it")
    assert "does not sell STBU" in reply.text and "stbu/safety" in reply.text
    assert model.calls == 0


async def test_answer_line_is_logged_with_cost(tmp_path, capsys):
    p = pipeline(tmp_path, FakeModel(["Stobox was founded in 2018."]))
    reply = await p.answer("When was Stobox founded?", chat="c", user="u")
    assert reply.outcome == "answered"
    line = [ln for ln in capsys.readouterr().out.splitlines() if '"event": "answer"' in ln][-1]
    rec = json.loads(line)
    assert rec["usd"] == 0.01 and rec["verdict"] == "no_contradiction_detected"


# --- rails port --------------------------------------------------------------

def test_rails_trust_only_site_addresses():
    out = ComplianceRails().post_process(
        f"Live {LIVE}, fake 0x{'1' * 40}, see https://stbu-claim.xyz and https://www.stobox.io/stbu",
        "where?", trusted=frozenset({LIVE.lower()})).text
    assert LIVE in out and "0x" + "1" * 40 not in out
    assert "stbu-claim.xyz" not in out and "stobox.io/stbu" in out


def test_rails_drop_raise_sentences():
    out, n = drop_raise_sentences("Stobox was founded in 2018. Its company raise cleared the due "
                                  "diligence review of Silicon Prairie. Products: three.")
    assert n == 1 and "Silicon Prairie" not in out and "founded in 2018" in out


def test_prices_table_covers_the_two_models():
    class U:
        input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 1000, 100, 0, 0
    assert round(cost_usd("claude-sonnet-5", U()), 6) == round((1000 * 2 + 100 * 10) / 1e6, 6)
    assert cost_usd("claude-haiku-4-5", U()) < cost_usd("claude-sonnet-5", U())


# --- leads and telegram helpers ---------------------------------------------

def test_own_mailboxes_are_never_leads():
    assert extract_email("write to support@stobox.io") is None
    assert extract_email("me: ann@acme.com, cc info@stobox.io") == "ann@acme.com"


async def test_lead_only_in_dm_once_and_with_issuer_intent(tmp_path):
    leads = Leads(tmp_path)
    kw = dict(user_id="1", name="Ann", text="we want to tokenize our fund, ann@acme.com", history=[])
    assert await leads.consider(private=False, **kw) is None
    first = await leads.consider(private=True, **kw)
    assert first and "ann@acme.com" in first
    assert await leads.consider(private=True, **kw) is None
    assert await leads.consider(private=True, user_id="2", name="B", text="hi bob@x.io", history=[]) is None


def test_rate_limit_and_split():
    rl = RateLimit(per_min=2, per_day=3)
    assert rl.allow("u", 0) and rl.allow("u", 1) and not rl.allow("u", 2)
    assert rl.allow("u", 61) and not rl.allow("u", 62)
    parts = split_for_telegram("a" * 5000 + "\n\n" + "b" * 10)
    assert all(len(p) <= 4096 for p in parts) and "".join(parts).count("b") == 10


async def test_chain_reader_marks_unreachable(tmp_path):
    def handler(request):
        return httpx.Response(500)
    reader = ChainReader(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    rows = await reader.balances("0x" + "2" * 40, LIVE, {"Arbitrum": "0x" + "3" * 40})
    assert all(r.balance is None for r in rows) and [r.chain for r in rows] == ["Base", "Arbitrum"]


def test_module_exports():
    assert answer_mod.CANT_VERIFY.startswith("I can't verify")
