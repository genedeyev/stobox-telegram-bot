"""Telegram slash commands — user + admin.

User commands mostly funnel into the RAG pipeline (so answers stay cited and
grounded). Admin commands operate the bot (reindex, stats, health, etc.) and are
gated on the configured admin user-id allowlist.
"""

from __future__ import annotations

from ...logging import get_logger

log = get_logger(__name__)

# Canned topic queries reuse the RAG pipeline so answers are always cited.
_TOPIC_QUERIES = {
    "roadmap": "What is on the Stobox roadmap according to the documentation?",
    "token": "Explain the STBU token and its utility.",
    "pricing": "What are Stobox pricing and plans?",
    "products": "What products does Stobox offer (Compass, Wallet, tokenization)?",
    "news": "What are the latest Stobox updates and announcements in the docs?",
    "events": "What Stobox events, AMAs, or webinars are documented?",
    "tutorial": "Give a beginner tutorial on getting started with Stobox tokenization.",
}


def _engine(context):
    return context.bot_data["engine"]


def _adapter(context):
    return context.bot_data["adapter"]


def _is_admin(update, context) -> bool:
    user = update.effective_user
    return bool(user and user.id in _adapter(context).admins)


# --------------------------------------------------------------------------- #
# User commands
# --------------------------------------------------------------------------- #
async def start_cmd(update, context) -> None:
    await update.effective_message.reply_text(
        "👋 I'm the official Stobox assistant. Stobox is a tokenization infrastructure "
        "company that helps businesses issue and manage tokenized real-world assets and "
        "securities.\n\n"
        "How can I help?\n"
        "• <b>Tokenize an asset</b> — tell me about it and I'll point you to the readiness "
        "check (/compass) and the team.\n"
        "• <b>STBU / STBX holder</b> — try /migrate, /valuation, or just ask.\n"
        "• <b>Learn about Stobox</b> — ask me anything; I answer from stobox.io.\n\n"
        "I share information, not investment advice. Verify me with /sources.",
        parse_mode="HTML", disable_web_page_preview=True,
    )


async def help_cmd(update, context) -> None:
    await update.effective_message.reply_text(
        "<b>Stobox assistant — commands</b>\n"
        "/migrate – STBU→Base migration explainer\n"
        "/compass – Stobox Compass + readiness check\n"
        "/valuation – company valuation (not a token price)\n"
        "/blog – latest posts + the weekly RWA digest\n"
        "/sources – official links to verify me\n"
        "/contact – reach the team / support\n"
        "/search &lt;query&gt; – search the knowledge base\n"
        "/report &lt;text&gt; · /feedback &lt;text&gt; · /about\n\n"
        "I answer from stobox.io's published content and cite sources. I do <b>not</b> give "
        "financial or investment advice, make price predictions, or handle seed phrases/keys.",
        parse_mode="HTML", disable_web_page_preview=True,
    )


async def about_cmd(update, context) -> None:
    engine = _engine(context)
    synced = engine.last_sync.strftime("%d %b %Y %H:%M UTC") if engine.last_sync else "unknown"
    await update.effective_message.reply_text(
        "I'm an AI assistant run by Stobox, grounded in stobox.io's published content and "
        "updated automatically when the site updates. Official pages and offering documents "
        "always take precedence over me — I can be wrong.\n\n"
        f"Knowledge last synced: {synced}. I don't give financial or legal advice.",
        disable_web_page_preview=True,
    )


async def docs_cmd(update, context) -> None:
    await update.effective_message.reply_text(
        "📚 Ask me anything about Stobox, or use /search <query>.\n"
        "Topics: /roadmap /token /pricing /products /news /events /tutorial"
    )


async def search_cmd(update, context) -> None:
    query = " ".join(context.args) if context.args else ""
    if not query:
        await update.effective_message.reply_text("Usage: /search <your question>")
        return
    await _adapter(context).process_query(update, context, query)


def _make_topic_handler(topic: str):
    async def handler(update, context) -> None:
        await _adapter(context).process_query(update, context, _TOPIC_QUERIES[topic])

    return handler


async def demo_cmd(update, context) -> None:
    await _adapter(context).process_query(
        update, context, "I'd like a demo of Stobox and to get started. How do we proceed?"
    )


def _canon(context):
    asm = getattr(_engine(context), "assembler", None)
    return asm.canonicals if asm else None


async def migrate_cmd(update, context) -> None:
    """STBU→Base migration explainer, straight from canonicals + freshness."""
    canon = _canon(context)
    if not canon:
        await update.effective_message.reply_text(
            "STBU migration details: please check https://stobox.io for the current guide."
        )
        return
    m = canon.get("tokens.stbu.migration", {})
    from ...guardrails.freshness import compute_migration_phase
    from ...guardrails.rails import IMPERSONATION_WARNING

    _, phase_text = compute_migration_phase(canon)
    lines = [
        "<b>STBU → Base migration</b>",
        f"Pattern: {m.get('pattern', 'burn-and-mint, 1:1, same-wallet only')}.",
        f"Destination chain: {m.get('destination_chain', 'Base')}.",
        f"Status: {phase_text}",
        f"Legacy V1 tokens: {m.get('legacy_v1', 'not eligible')}.",
        "Consolidate all STBU to ONE wallet before migrating.",
        "Confirm the exact burn address via official Stobox channels only.",
        "",
        IMPERSONATION_WARNING,
    ]
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML",
                                              disable_web_page_preview=True)


async def compass_cmd(update, context) -> None:
    canon = _canon(context)
    if canon:
        c = canon.get("products.compass", {})
        text = (
            f"<b>{c.get('name', 'Stobox Compass')}</b>\n"
            f"{c.get('what', 'Tokenization readiness platform')}.\n"
            f"{(c.get('chains_phrasing') or '').strip()}\n"
            "Run the readiness check: https://stobox.io/compass"
        )
    else:
        text = "Stobox Compass — tokenization readiness platform: https://stobox.io/compass"
    await update.effective_message.reply_text(text, parse_mode="HTML",
                                              disable_web_page_preview=True)


async def valuation_cmd(update, context) -> None:
    engine = _engine(context)
    fresh = engine.build_freshness()
    val_line = next((ln for ln in fresh.splitlines() if "valuation" in ln.lower()),
                    "See https://stobox.io/valuation for the current mark.")
    await update.effective_message.reply_text(
        "<b>Stobox company valuation</b>\n"
        f"{val_line.lstrip('- ')}\n"
        "Note: this is a COMPANY valuation (Eqvista) — not the STBX token price, not an "
        "offer, not investment advice.",
        parse_mode="HTML", disable_web_page_preview=True,
    )


async def sources_cmd(update, context) -> None:
    canon = _canon(context)
    links = canon.get("official_links", {}) if canon else {}
    default = {
        "website": "https://stobox.io", "x": "https://x.com/StoboxCompany",
        "linkedin": "https://www.linkedin.com/company/stobox/",
        "telegram": "https://t.me/stobox_community",
        "youtube": "https://www.youtube.com/@stobox",
        "github": "https://github.com/StoboxTechnologies",
        "support_email": "support@stobox.io",
    }
    links = links or default
    order = ["website", "app", "x", "linkedin", "telegram", "youtube", "github", "support_email"]
    lines = ["<b>Official Stobox links</b> — verify me against these:"]
    for k in order:
        if links.get(k):
            lines.append(f"{k}: {links[k]}")
    lines.append("\nStobox staff never DM you first. Anything else is not us.")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML",
                                              disable_web_page_preview=True)


async def blog_cmd(update, context) -> None:
    """Point readers at the blog + the freshest posts from the index."""
    engine = _engine(context)
    lines = [
        "📰 <b>The Stobox Blog</b> — tokenization news, deep dives, and the weekly "
        "<b>RWA &amp; Tokenization Digest</b>:",
        "https://www.stobox.io/blog",
    ]
    if engine.blog_posts:
        lines.append("\nLatest:")
        lines += [f"• {p['title'][:80]} — {p['url']}" for p in engine.blog_posts[:5]]
    lines.append("\nAsk me about anything you read — I'll pull up the details.")
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode="HTML", disable_web_page_preview=True
    )


async def contact_cmd(update, context) -> None:
    await update.effective_message.reply_text(
        "📬 Support (holders): support@stobox.io\n"
        "Issuers exploring tokenization: run the readiness check at https://stobox.io/compass "
        "or ask here and I'll help you get to the team."
    )


async def support_cmd(update, context) -> None:
    adapter = _adapter(context)
    for admin_id in adapter.admins:
        try:
            await context.bot.send_message(
                admin_id,
                f"🆘 Support request from @{update.effective_user.username or update.effective_user.id} "
                f"in {update.effective_chat.title or 'private'}.",
            )
        except Exception:  # noqa: BLE001
            pass
    await update.effective_message.reply_text(
        "I've flagged the team for you. Someone will follow up. Meanwhile I can try to help — just ask."
    )


async def report_cmd(update, context) -> None:
    text = " ".join(context.args) if context.args else "(no details)"
    for admin_id in _adapter(context).admins:
        try:
            await context.bot.send_message(admin_id, f"⚠️ Report: {text[:500]}")
        except Exception:  # noqa: BLE001
            pass
    await update.effective_message.reply_text("Thanks — your report has been sent to the team.")


async def feedback_cmd(update, context) -> None:
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.effective_message.reply_text("Usage: /feedback <your feedback>")
        return
    log.info("feedback", user=update.effective_user.id, text=text[:500])
    await update.effective_message.reply_text("🙏 Thank you for the feedback!")


async def language_cmd(update, context) -> None:
    await update.effective_message.reply_text(
        "🌍 I auto-detect your language and reply in it. Just write in your language."
    )


async def status_cmd(update, context) -> None:
    engine = _engine(context)
    n = await engine.retriever.store.count()
    await update.effective_message.reply_text(
        f"✅ Online.\nIndexed chunks: {n}\nReasoner: {engine.reasoner.name}/{engine.reasoner.model}"
    )


# --------------------------------------------------------------------------- #
# Admin commands
# --------------------------------------------------------------------------- #
async def reindex_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    engine = _engine(context)
    await update.effective_message.reply_text("♻️ Reindexing…")
    n = await engine.indexer.index_directory(
        engine.config.get("knowledge.docs_path", "docs"), rebuild=True
    )
    await update.effective_message.reply_text(f"✅ Reindexed. {n} chunks.")


async def stats_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    snap = _engine(context).decisions.snapshot()
    lines = [f"{k}: {v}" for k, v in snap.items()]
    await update.effective_message.reply_text("📊 Stats\n" + "\n".join(lines))


async def health_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    engine = _engine(context)
    n = await engine.retriever.store.count()
    await update.effective_message.reply_text(
        f"🩺 Health\nchunks={n}\nreasoner={engine.reasoner.name}\n"
        f"classifier={engine.classifier.name}\nembedder={engine.retriever.embedder.name}"
    )


async def prompts_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    lib = _engine(context).prompts
    ids = ", ".join(sorted(lib._cache.keys())) or "(none)"
    await update.effective_message.reply_text(f"🧾 Prompts: {ids}")


async def reload_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    from ...prompts import get_prompts

    get_prompts.cache_clear()
    _engine(context).prompts = get_prompts()
    await update.effective_message.reply_text("🔄 Prompts reloaded.")


async def memory_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    store = _engine(context).memory
    n = len(getattr(store, "_profiles", {}))
    await update.effective_message.reply_text(f"🧠 Known user profiles (cached): {n}")


async def digest_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    engine = _engine(context)
    builder = engine.daily_digest()
    digest = builder.build()
    narrative = await builder.narrative(digest)
    await update.effective_message.reply_text(
        builder.render_text(digest, narrative)[:4096], parse_mode="Markdown"
    )


async def faq_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    engine = _engine(context)
    await update.effective_message.reply_text("🧩 Generating weekly FAQ from recent questions…")
    entries = await engine.weekly_faq().generate(top_n=8)
    if not entries:
        await update.effective_message.reply_text("Not enough questions yet to build an FAQ.")
        return
    from ...insights import WeeklyFAQ

    md = WeeklyFAQ.render_markdown(entries)
    await update.effective_message.reply_text(md[:4096])


async def pending_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    entries = _engine(context).qa.pending()
    if not entries:
        await update.effective_message.reply_text("✅ No open community questions.")
        return
    lines = ["❓ <b>Open community questions</b>"]
    for e in entries[:15]:
        lines.append(f"\n<b>#{e.qid}</b> ({e.ask_count}× · {e.created})\n“{e.question[:200]}”")
    lines.append("\nAnswer with: <code>/answer &lt;id&gt; &lt;text&gt;</code>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def answer_cmd(update, context) -> None:
    """Admin provides the canonical answer: /answer <id> <text>.
    Saves APPROVED to the register, hot-loads it into knowledge, and delivers
    the answer to everyone who asked."""
    if not _is_admin(update, context):
        return
    import asyncio as _asyncio
    from pathlib import Path

    from ...qa import mirror

    engine = _engine(context)
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /answer <id> <answer text>")
        return
    qid = int(context.args[0])
    text = " ".join(context.args[1:]).strip()
    entry = engine.qa.get(qid)
    if not entry:
        await update.effective_message.reply_text(f"No question #{qid}. See /pending.")
        return
    if entry.status == "answered":
        await update.effective_message.reply_text(f"#{qid} was already answered.")
        return

    entry = engine.qa.answer(qid, text)

    # 1) APPROVED into the stobox-v15 register (source of truth, best-effort).
    number = await _asyncio.to_thread(mirror.push_approved, entry)
    if number:
        entry.register_number = number
        engine.qa._save()

    # 2) Into local knowledge NOW — the docs watcher hot-reloads it, so the bot
    #    starts answering with Gene's wording immediately.
    try:
        qa_file = Path(engine.config.get("knowledge.docs_path", "docs")) / "community-qa.md"
        section = f"\n## {number or ('T' + str(qid))}. {entry.question}\n\n**Answer:**\n\n{text}\n"
        with qa_file.open("a", encoding="utf-8") as f:
            f.write(section)
    except Exception as exc:  # noqa: BLE001
        await update.effective_message.reply_text(f"⚠️ Local knowledge append failed: {exc}")

    # 3) Deliver to everyone who asked (their own conversation — not cold contact).
    delivered = 0
    for asker in entry.askers:
        chat_id = asker["chat_id"]
        if chat_id.startswith("inline:"):
            chat_id = chat_id.split(":", 1)[1]   # DM the inline user directly
        followup = (
            f"👋 Earlier you asked:\n“{entry.question}”\n\n"
            f"Here's the confirmed answer from the Stobox team:\n\n{text}"
        )
        try:
            await context.bot.send_message(chat_id, followup[:4096])
            delivered += 1
        except Exception:  # noqa: BLE001 - user may have blocked the bot etc.
            pass

    await update.effective_message.reply_text(
        f"✅ #{qid} saved{' to register §' + str(number) if number else ' (register mirror failed — kept locally)'}, "
        f"knowledge updated, delivered to {delivered}/{len(entry.askers)} asker(s)."
    )


async def pause_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    reason = " ".join(context.args) if context.args else "manual"
    _engine(context).pause(reason)
    await update.effective_message.reply_text(
        f"⏸️ Bot PAUSED ({reason}). It will answer only with static FAQ + contact info. "
        "Use /resume to restore."
    )


async def resume_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    _engine(context).resume()
    await update.effective_message.reply_text("▶️ Bot RESUMED. Full answering restored.")


async def sync_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    engine = _engine(context)
    await update.effective_message.reply_text(
        "🌐 Syncing knowledge from stobox.io + GitHub… this can take a minute."
    )
    try:
        results = await engine.sync_knowledge()
    except Exception as exc:  # noqa: BLE001
        await update.effective_message.reply_text(f"Sync failed: {exc}")
        return
    detail = ", ".join(f"{k}: {v} chunks" for k, v in results.items()) or "no sources enabled"
    n = await engine.retriever.store.count()
    await update.effective_message.reply_text(
        f"✅ Sync done ({detail}). Index now holds {n} chunks."
    )


async def gaps_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    from ...insights import documentation_gaps

    gaps = documentation_gaps(_engine(context).decisions.records())
    if not gaps:
        await update.effective_message.reply_text("✅ No documentation gaps detected recently.")
        return
    lines = ["⚠️ *Documentation gaps* (frequent, low-confidence):"]
    lines += [f"• {g.representative} ({g.count}× · conf {g.avg_confidence})" for g in gaps[:10]]
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def admin_cmd(update, context) -> None:
    if not _is_admin(update, context):
        await update.effective_message.reply_text("Not authorized.")
        return
    await update.effective_message.reply_text(
        "🔐 Admin: /pending /answer /pause /resume /sync /reindex /stats /health "
        "/digest /faq /gaps /prompts /reload /memory /export /logs /debug /test /cache /users"
    )


async def _admin_stub(update, context, label: str) -> None:
    if not _is_admin(update, context):
        return
    await update.effective_message.reply_text(f"[{label}] — see /stats and /health for live data.")


def registry() -> dict:
    handlers = {
        "help": help_cmd,
        "start": start_cmd,
        "about": about_cmd,
        "docs": docs_cmd,
        "search": search_cmd,
        "demo": demo_cmd,
        "migrate": migrate_cmd,
        "compass": compass_cmd,
        "valuation": valuation_cmd,
        "sources": sources_cmd,
        "blog": blog_cmd,
        "contact": contact_cmd,
        "support": support_cmd,
        "report": report_cmd,
        "feedback": feedback_cmd,
        "language": language_cmd,
        "status": status_cmd,
        # admin
        "reindex": reindex_cmd,
        "stats": stats_cmd,
        "health": health_cmd,
        "prompts": prompts_cmd,
        "reload": reload_cmd,
        "memory": memory_cmd,
        "digest": digest_cmd,
        "faq": faq_cmd,
        "gaps": gaps_cmd,
        "sync": sync_cmd,
        "resync": sync_cmd,
        "pause": pause_cmd,
        "resume": resume_cmd,
        "pending": pending_cmd,
        "answer": answer_cmd,
        "admin": admin_cmd,
    }
    for topic in _TOPIC_QUERIES:
        handlers[topic] = _make_topic_handler(topic)
    for label in ("export", "logs", "debug", "test", "cache", "users"):
        handlers[label] = (lambda lbl: lambda u, c: _admin_stub(u, c, lbl))(label)
    return handlers
