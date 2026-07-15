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
    # Deep-link attribution: t.me/bot?start=<source> — first touch wins.
    # ref_<userid> payloads additionally credit the referrer.
    if context.args:
        payload = context.args[0][:64]
        engine = _engine(context)
        user = update.effective_user
        try:
            profile = await engine.memory.get_profile(f"telegram:{user.id}", user.full_name)
            if not profile.source:
                profile.source = payload
                await engine.memory.save_profile(profile)
            if payload.startswith("ref_") and payload[4:].isdigit() \
                    and payload[4:] != str(user.id):
                referrer = await engine.memory.get_profile(f"telegram:{payload[4:]}")
                referrer.referrals += 1
                await engine.memory.save_profile(referrer)
        except Exception:  # noqa: BLE001 - attribution must never break /start
            pass
    await update.effective_message.reply_text(
        "👋 I'm the official Stobox assistant. Stobox is a tokenization infrastructure "
        "company that helps businesses issue and manage tokenized real-world assets and "
        "securities.\n\n"
        "How can I help?\n"
        "• <b>Tokenize an asset</b> — tell me about it and I'll point you to the readiness "
        "check (/compass) and the team.\n"
        "• <b>STBU / STBX holder</b> — try /migrate, /valuation, or /remindme for "
        "migration-deadline reminders.\n"
        "• <b>Learn about Stobox</b> — ask me anything; I answer from stobox.io.\n\n"
        "I share information, not investment advice. Verify me with /sources.",
        parse_mode="HTML", disable_web_page_preview=True,
    )


async def remindme_cmd(update, context) -> None:
    """Opt in to STBU migration deadline reminders (DM only)."""
    engine = _engine(context)
    chat = update.effective_chat
    if chat.type != "private":
        await update.effective_message.reply_text(
            "Reminders are personal — DM me /remindme and I'll keep you posted. 👍"
        )
        return
    from ...guardrails.freshness import compute_migration_phase

    canon = getattr(engine.assembler, "canonicals", None) if engine.assembler else None
    phase_text = compute_migration_phase(canon)[1] if canon else "See stobox.io for status."
    new = engine.reminders.subscribe(str(chat.id))
    await update.effective_message.reply_text(
        ("✅ You're on the list — I'll remind you before the STBU migration deadline "
         "(and when claims open), right here.\n\n" if new else
         "You're already subscribed — I've got you. 👍\n\n")
        + f"Current status: {phase_text}\n\nStop anytime with /stopreminders.",
        disable_web_page_preview=True,
    )


async def stopreminders_cmd(update, context) -> None:
    removed = _engine(context).reminders.unsubscribe(str(update.effective_chat.id))
    await update.effective_message.reply_text(
        "Done — no more reminders. You can rejoin anytime with /remindme."
        if removed else "You weren't subscribed — nothing to stop. 🙂"
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
    from collections import Counter

    engine = _engine(context)
    snap = engine.decisions.snapshot()
    lines = [f"{k}: {v}" for k, v in snap.items()]
    # Growth: acquisition sources (deep links) + top referrers + reminder opt-ins.
    profiles = list(getattr(engine.memory, "_profiles", {}).values())
    sources = Counter(p.source for p in profiles if p.source)
    if sources:
        lines.append("acquisition_sources: " + ", ".join(f"{s}={n}" for s, n in sources.most_common(8)))
    referrers = [(p.display_name or p.user_key, p.referrals) for p in profiles if p.referrals]
    if referrers:
        top = sorted(referrers, key=lambda x: x[1], reverse=True)[:5]
        lines.append("top_referrers: " + ", ".join(f"{n}({c})" for n, c in top))
    lines.append(f"reminder_subscribers: {len(engine.reminders.subscribers)}")
    lines.append(f"open_questions: {len(engine.qa.pending())}")
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


def _reply_target(update):
    """The user an admin is acting on: the replied-to message's author."""
    r = update.effective_message.reply_to_message
    return r.from_user if r else None


async def strikes_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    target = _reply_target(update)
    if not target and context.args and context.args[0].lstrip("-").isdigit():
        uid = context.args[0]
    elif target:
        uid = str(target.id)
    else:
        await update.effective_message.reply_text(
            "Reply to a user's message with /strikes (or /strikes <user_id>)."
        )
        return
    rec = _engine(context).strikes.record(f"telegram:{uid}")
    if not rec or not rec.strikes:
        await update.effective_message.reply_text(f"User {uid}: clean record. ✅")
        return
    from collections import Counter
    cats = Counter(s.category for s in rec.strikes)
    lines = [f"🛡 <b>{rec.display_name or uid}</b> — {len(rec.strikes)} total strike(s)"
             + (" · <b>BANNED</b>" if rec.banned else "")]
    lines += [f"• {c}: {n}" for c, n in cats.most_common()]
    lines.append("\n/warn /mute /ban (reply) · /clearstrikes <id> to reset")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def _manual_action(update, context, action: str, minutes: int = 0) -> None:
    if not _is_admin(update, context):
        return
    target = _reply_target(update)
    if not target:
        await update.effective_message.reply_text(
            f"Reply to the user's message with /{action}."
        )
        return
    engine = _engine(context)
    chat = update.effective_chat
    uk = f"telegram:{target.id}"
    try:
        if action == "warn":
            engine.strikes.add(uk, "manual", display_name=target.full_name, chat_id=str(chat.id))
            await context.bot.send_message(
                target.id, "⚠️ A Stobox community admin has warned you. Please keep it "
                           "respectful and constructive. Reply /appeal to contest."
            )
            await update.effective_message.reply_text(f"⚠️ Warned {target.full_name}.")
        elif action == "mute":
            from datetime import UTC, datetime, timedelta

            from telegram import ChatPermissions
            until = datetime.now(UTC) + timedelta(minutes=minutes or 60)
            await context.bot.restrict_chat_member(
                chat.id, target.id, ChatPermissions(can_send_messages=False), until_date=until
            )
            engine.strikes.add(uk, "manual", display_name=target.full_name, chat_id=str(chat.id))
            await update.effective_message.reply_text(f"🔇 Muted {target.full_name} for {minutes or 60} min.")
        elif action == "unmute":
            from telegram import ChatPermissions
            await context.bot.restrict_chat_member(
                chat.id, target.id,
                ChatPermissions(can_send_messages=True, can_send_polls=True,
                                can_send_other_messages=True, can_add_web_page_previews=True),
            )
            await update.effective_message.reply_text(f"🔊 Unmuted {target.full_name}.")
        elif action == "ban":
            await context.bot.ban_chat_member(chat.id, target.id)
            engine.strikes.set_banned(uk, True, target.full_name)
            await update.effective_message.reply_text(f"⛔ Banned {target.full_name}.")
    except Exception as exc:  # noqa: BLE001
        await update.effective_message.reply_text(f"Action failed (admin rights?): {exc}")


async def warn_cmd(update, context):
    await _manual_action(update, context, "warn")

async def mute_cmd(update, context):
    mins = int(context.args[0]) if context.args and context.args[0].isdigit() else 60
    await _manual_action(update, context, "mute", mins)

async def unmute_cmd(update, context):
    await _manual_action(update, context, "unmute")

async def ban_cmd(update, context):
    await _manual_action(update, context, "ban")


async def unban_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.effective_message.reply_text("Usage: /unban <user_id>")
        return
    uid = context.args[0]
    engine = _engine(context)
    engine.strikes.pardon(f"telegram:{uid}")
    freed = 0
    for chat_id in getattr(_adapter(context), "known_chats", set()):
        try:
            await context.bot.unban_chat_member(chat_id, int(uid), only_if_banned=True)
            freed += 1
        except Exception:  # noqa: BLE001
            pass
    await update.effective_message.reply_text(f"✅ Unbanned {uid} in {freed} chat(s), strike cleared.")


async def clearstrikes_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    target = _reply_target(update)
    uid = str(target.id) if target else (context.args[0] if context.args else None)
    if not uid:
        await update.effective_message.reply_text("Reply to a user or /clearstrikes <user_id>.")
        return
    _engine(context).strikes.clear(f"telegram:{uid}")
    await update.effective_message.reply_text(f"🧹 Cleared record for {uid}.")


async def modstats_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    s = _engine(context).strikes.stats()
    lines = ["🛡 <b>Moderation</b>",
             f"users with active strikes: {s['users_with_strikes']}",
             f"active strikes: {s['active_strikes']} · banned: {s['banned']}"]
    if s["by_category"]:
        lines.append("by category: " + ", ".join(f"{k}={v}" for k, v in
                     sorted(s["by_category"].items(), key=lambda x: -x[1])))
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def appeal_cmd(update, context) -> None:
    """User contests a moderation action → routed to admins."""
    user = update.effective_user
    reason = " ".join(context.args) if context.args else "(no details given)"
    for admin_id in _adapter(context).admins:
        try:
            await context.bot.send_message(
                admin_id,
                f"📣 <b>Appeal</b> from {user.full_name} (id {user.id}):\n“{reason[:400]}”\n"
                f"Pardon: /unban {user.id}  ·  Record: reply /strikes",
                parse_mode="HTML",
            )
        except Exception:  # noqa: BLE001
            pass
    await update.effective_message.reply_text(
        "Thanks — your appeal has been sent to a human admin. We'll review it fairly."
    )


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
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /answer <id> <answer text>")
        return
    await _finalize_answer(update, context, int(context.args[0]),
                           " ".join(context.args[1:]).strip())


async def approve_cmd(update, context) -> None:
    """One-tap approval of the bot's proposed draft: /approve <id>."""
    if not _is_admin(update, context):
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /approve <id>")
        return
    qid = int(context.args[0])
    entry = _engine(context).qa.get(qid)
    if not entry or not entry.draft:
        await update.effective_message.reply_text(
            f"#{qid} has no draft to approve — use /answer {qid} <text>."
        )
        return
    await _finalize_answer(update, context, qid, entry.draft)


async def _finalize_answer(update, context, qid: int, text: str) -> None:
    """Shared /answer + /approve flow: register → knowledge → deliver."""
    import asyncio as _asyncio
    from pathlib import Path

    from ...qa import mirror

    engine = _engine(context)
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
        "/digest /faq /gaps /prompts /reload /memory /export /logs /debug /test /cache /users\n"
        "🛡 Moderation (reply to a user): /warn /mute [min] /unmute /ban · "
        "/unban &lt;id&gt; /strikes /clearstrikes /modstats"
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
        "approve": approve_cmd,
        "remindme": remindme_cmd,
        "stopreminders": stopreminders_cmd,
        # moderation
        "strikes": strikes_cmd,
        "warn": warn_cmd,
        "mute": mute_cmd,
        "unmute": unmute_cmd,
        "ban": ban_cmd,
        "unban": unban_cmd,
        "clearstrikes": clearstrikes_cmd,
        "modstats": modstats_cmd,
        "appeal": appeal_cmd,
        "admin": admin_cmd,
    }
    for topic in _TOPIC_QUERIES:
        handlers[topic] = _make_topic_handler(topic)
    for label in ("export", "logs", "debug", "test", "cache", "users"):
        handlers[label] = (lambda lbl: lambda u, c: _admin_stub(u, c, lbl))(label)
    return handlers
