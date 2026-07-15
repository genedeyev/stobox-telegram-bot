"""Render the analytics digest as a self-contained HTML dashboard.

No external assets, no JS frameworks — inline CSS only, theme-aware, safe to
serve behind an auth gateway. Everything user-derived (questions, categories) is
HTML-escaped. Shape mirrors DailyDigest.build().
"""

from __future__ import annotations

from html import escape
from typing import Any


def _bar(pct: float) -> str:
    pct = max(0.0, min(100.0, pct))
    return f'<span class="bar"><span style="width:{pct:.0f}%"></span></span>'


def _rows(items: list[list[str]]) -> str:
    return "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in items
    )


def render_dashboard(digest: dict[str, Any]) -> str:
    if digest.get("empty") or not digest.get("count"):
        body = '<div class="empty">No activity recorded yet. Check back once Stoby has been chatting.</div>'
        return _page(body)

    s = digest.get("sentiment", {})
    label = s.get("label", "—")
    health = float(s.get("health_score", 0)) * 100
    metrics = digest.get("metrics", {})

    # Community health hero.
    health_cls = {"healthy": "good", "watch": "warn", "at-risk": "bad"}.get(label, "warn")
    cards = [
        ("Community health", f'<span class="pill {health_cls}">{escape(label)}</span> {health:.0f}%'),
        ("Conversations", str(digest.get("count", 0))),
        ("Unanswered rate", f'{s.get("unanswered_rate", 0):.0%}'),
        ("Moderation rate", f'{s.get("moderation_rate", 0):.0%}'),
        ("Escalations", str(digest.get("escalations", 0))),
    ]
    card_html = "".join(
        f'<div class="card"><div class="k">{escape(k)}</div><div class="v">{v}</div></div>'
        for k, v in cards
    )

    top = digest.get("top_questions", [])
    top_rows = _rows([
        [escape(q["question"][:90]), str(q["asked"]), escape(", ".join(q.get("topics", [])[:3]))]
        for q in top
    ]) or '<tr><td colspan="3" class="muted">None yet</td></tr>'

    gaps = digest.get("documentation_gaps", [])
    gap_rows = _rows([
        [escape(g["question"][:90]), str(g["asked"]), str(g["unresolved"]),
         f'{g["avg_confidence"]:.0%}']
        for g in gaps
    ]) or '<tr><td colspan="4" class="muted">No gaps — nice.</td></tr>'

    leads = digest.get("potential_leads", [])
    lead_rows = _rows([
        [escape(str(ld.get("user_key", "—"))[:40]), str(ld.get("touches", 0)),
         "✅" if ld.get("captured") else "—", escape(str(ld.get("last_q", ""))[:70])]
        for ld in leads
    ]) or '<tr><td colspan="4" class="muted">None yet</td></tr>'

    langs = digest.get("languages", [])
    total_lang = sum(n for _, n in langs) or 1
    lang_rows = _rows([
        [escape(code), _bar(n / total_lang * 100) + f" {n}"] for code, n in langs[:8]
    ]) or '<tr><td colspan="2" class="muted">None yet</td></tr>'

    mods = digest.get("moderation_actions", [])
    mod_counts: dict[str, int] = {}
    for m in mods:
        key = f'{m.get("category", "?")} → {m.get("action", "?")}'
        mod_counts[key] = mod_counts.get(key, 0) + 1
    mod_rows = _rows([[escape(k), str(v)] for k, v in
                      sorted(mod_counts.items(), key=lambda x: -x[1])]) \
        or '<tr><td colspan="2" class="muted">Quiet — no actions</td></tr>'

    body = f"""
    <h1>Stoby — Community Analytics</h1>
    <p class="sub">Live view over the decision log · {digest.get('count', 0)} recent conversations</p>
    <div class="cards">{card_html}</div>

    <section>
      <h2>🔥 Top questions</h2>
      <table><thead><tr><th>Question</th><th>Asked</th><th>Topics</th></tr></thead>
      <tbody>{top_rows}</tbody></table>
    </section>

    <section>
      <h2>🕳 Documentation gaps <span class="muted">— recurring & low-confidence</span></h2>
      <table><thead><tr><th>Question</th><th>Asked</th><th>Unresolved</th><th>Avg conf.</th></tr></thead>
      <tbody>{gap_rows}</tbody></table>
    </section>

    <div class="two-col">
      <section>
        <h2>🎯 Potential leads</h2>
        <table><thead><tr><th>User</th><th>Touches</th><th>Captured</th><th>Last question</th></tr></thead>
        <tbody>{lead_rows}</tbody></table>
      </section>
      <section>
        <h2>🌍 Languages</h2>
        <table><tbody>{lang_rows}</tbody></table>
        <h2 style="margin-top:1.4rem">🛡 Moderation</h2>
        <table><tbody>{mod_rows}</tbody></table>
      </section>
    </div>

    <details class="raw"><summary>Raw metrics</summary><pre>{escape(_fmt(metrics))}</pre></details>
    """
    return _page(body)


def _fmt(d: dict) -> str:
    return "\n".join(f"{k}: {v}" for k, v in d.items())


def _page(body: str) -> str:
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>Stoby Analytics</title>
<style>
:root {{ color-scheme: light dark;
  --bg:#f6f7f9; --panel:#fff; --ink:#1a1d21; --muted:#6b7280; --line:#e5e7eb;
  --accent:#5b8def; --good:#12a150; --warn:#c98a00; --bad:#d33; }}
@media (prefers-color-scheme: dark) {{ :root {{
  --bg:#0f1216; --panel:#171b21; --ink:#e8eaed; --muted:#9aa4b2; --line:#252b33;
  --accent:#6b9bff; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; padding:2rem; }}
h1 {{ margin:0 0 .2rem; font-size:1.6rem; }}
h2 {{ font-size:1.05rem; margin:0 0 .6rem; }}
.sub {{ color:var(--muted); margin:0 0 1.4rem; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:.8rem; margin-bottom:1.6rem; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:1rem; }}
.card .k {{ color:var(--muted); font-size:.8rem; text-transform:uppercase; letter-spacing:.03em; }}
.card .v {{ font-size:1.5rem; font-weight:650; margin-top:.3rem; }}
section {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:1.1rem 1.2rem; margin-bottom:1.2rem; }}
.two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:1.2rem; }}
@media (max-width:760px) {{ .two-col {{ grid-template-columns:1fr; }} body {{ padding:1rem; }} }}
table {{ width:100%; border-collapse:collapse; font-size:.9rem; }}
th,td {{ text-align:left; padding:.45rem .5rem; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ color:var(--muted); font-weight:600; font-size:.78rem; text-transform:uppercase; letter-spacing:.03em; }}
tr:last-child td {{ border-bottom:none; }}
.muted {{ color:var(--muted); }}
.pill {{ display:inline-block; padding:.1rem .5rem; border-radius:999px; font-size:.8rem; font-weight:600; color:#fff; }}
.pill.good {{ background:var(--good); }} .pill.warn {{ background:var(--warn); }} .pill.bad {{ background:var(--bad); }}
.bar {{ display:inline-block; width:90px; height:8px; background:var(--line); border-radius:4px; overflow:hidden; vertical-align:middle; margin-right:.4rem; }}
.bar span {{ display:block; height:100%; background:var(--accent); }}
.empty {{ text-align:center; color:var(--muted); padding:4rem 1rem; }}
.raw {{ color:var(--muted); font-size:.85rem; }}
.raw pre {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:1rem; overflow:auto; }}
</style></head><body>{body}
<p class="muted" style="text-align:center;margin-top:2rem;font-size:.8rem">Auto-refreshes every 60s · Stoby, the resident AI of the Stobox community</p>
</body></html>"""
