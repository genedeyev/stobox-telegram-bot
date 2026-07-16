"""Telegram slash commands — user + admin.

User commands mostly funnel into the RAG pipeline (so answers stay cited and
grounded). Admin commands operate the bot (reindex, stats, health, etc.) and are
gated on the configured admin user-id allowlist.
"""

from __future__ import annotations

from html import escape as html_escape

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
    return _adapter(context).is_admin(update.effective_user)


# --------------------------------------------------------------------------- #
# Interactive user guide (/guide) — button-navigated "what can Stoby do"
# --------------------------------------------------------------------------- #
_GUIDE_MENU = (
    "🧭 <b>Stoby — quick guide</b>\n\n"
    "I'm the resident AI of the Stobox community. Tap a topic to see what I can do:"
)
_GUIDE_SECTIONS = {
    "ask": (
        "💬 <b>Ask me anything</b>\n\n"
        "Just type your question — Stobox, tokenization, RWAs, Compass, STV3, ERC-3643, "
        "jurisdictions, anything. I answer from the official docs and show my sources.\n\n"
        "• Short answers by default — tap <b>📖 More detail</b> for the deep dive\n"
        "• Tap <b>📩 Email me this</b> to get a full write-up by email\n"
        "• In groups, @mention me or reply to my message\n\n"
        "Try: <i>“What is Stobox Compass?”</i>"
    ),
    "migration": (
        "🪙 <b>STBU &amp; migration</b>\n\n"
        "• /migrate — the STBU→Base migration, step by step\n"
        "• /check — paste your <b>public</b> wallet address and I'll show your STBU across "
        "every chain + your exact path (read-only, I never ask for keys)\n"
        "• /remindme — reminders before the migration deadline\n"
        "• /valuation — the company valuation (not a token price)\n\n"
        "⚠️ Stobox staff never DM you first. Never share your seed phrase or private key."
    ),
    "tokenize": (
        "🏢 <b>Tokenize an asset</b>\n\n"
        "Exploring tokenization for your company, fund, or real estate?\n"
        "• /qualify — a quick 30-second fit check (5 taps)\n"
        "• /resources — the right guides for your asset &amp; jurisdiction\n"
        "• /compass — run the free Readiness Score (25 questions, no card)\n"
        "• Tell me about your asset and I'll point you to the right path\n"
        "• /contact — reach the Stobox team\n\n"
        "Stobox's three layers: <b>Intelligence</b> (organize) → <b>Raisable</b> (raise) → "
        "<b>Compass</b> (tokenize)."
    ),
    "community": (
        "🏆 <b>Community &amp; rewards</b>\n\n"
        "Take part, earn XP, climb the leaderboard:\n"
        "• /rank — your XP, level &amp; streak\n"
        "• /leaderboard — this week's top members\n"
        "• /ama — submit a question for the next community AMA\n"
        "• /subscribe — pick topics (migration · RWA news · product) and I'll DM you "
        "the moment something ships\n"
        "• Answer quiz nights for bonus XP · keep a daily streak!\n\n"
        "Share a good answer with the ↗️ button to help the community grow."
    ),
    "verify": (
        "🔗 <b>Verify &amp; get help</b>\n\n"
        "• /sources — the official Stobox links (verify I'm the real me)\n"
        "• /contact — reach support / the team\n"
        "• /help — the full command list\n"
        "• /report — report an issue · /feedback — send feedback\n\n"
        "I'm an AI — I can be wrong. Official pages and offering documents always take "
        "precedence, and I don't give financial or legal advice."
    ),
}


def guide_view(section: str = "menu"):
    """Return (text, InlineKeyboardMarkup) for the guide menu or a section."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    if section == "menu" or section not in _GUIDE_SECTIONS:
        rows = [
            [InlineKeyboardButton("💬 Ask me anything", callback_data="guide:ask"),
             InlineKeyboardButton("🪙 STBU & migration", callback_data="guide:migration")],
            [InlineKeyboardButton("🏢 Tokenize an asset", callback_data="guide:tokenize"),
             InlineKeyboardButton("🏆 Community & rewards", callback_data="guide:community")],
            [InlineKeyboardButton("🔗 Verify & get help", callback_data="guide:verify")],
        ]
        return _GUIDE_MENU, InlineKeyboardMarkup(rows)
    back = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to guide", callback_data="guide:menu")]])
    return _GUIDE_SECTIONS[section], back


async def guide_cmd(update, context) -> None:
    text, markup = guide_view("menu")
    await update.effective_message.reply_text(
        text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True
    )


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
                engine.xp.award(f"telegram:{payload[4:]}", 25, "referral",
                                referrer.display_name or "")
        except Exception:  # noqa: BLE001 - attribution must never break /start
            pass
    await update.effective_message.reply_text(
        "👋 I'm <b>Stoby</b> — the resident AI of the Stobox community. Part monster, part "
        "mind, fully awake. Stobox is a tokenization infrastructure company that helps "
        "businesses issue and manage tokenized real-world assets and securities.\n\n"
        "How can I help?\n"
        "• <b>Tokenize an asset</b> — tell me about it and I'll point you to the readiness "
        "check (/compass) and the team.\n"
        "• <b>STBU / STBX holder</b> — try /migrate, /valuation, or /remindme for "
        "migration-deadline reminders.\n"
        "• <b>Learn about Stobox</b> — ask me anything; I answer from stobox.io.\n\n"
        "New here? Tap /guide for a quick tour. I share information, not investment "
        "advice — verify me with /sources.",
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


def _ama_button(qid: int, votes: int):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"👍 Upvote ({votes})", callback_data=f"ama:up:{qid}")
    ]])


async def ama_cmd(update, context) -> None:
    """Submit a question for the next AMA (during an open collection window)."""
    engine = _engine(context)
    ama = engine.ama
    text = " ".join(context.args).strip() if context.args else ""
    if not ama.open:
        await update.effective_message.reply_text(
            "No AMA is collecting right now. I'll announce the next one — stay tuned! 📢"
        )
        return
    if len(text) < 8:
        await update.effective_message.reply_text(
            "Ask away! Format: <code>/ama your question for the team</code>", parse_mode="HTML"
        )
        return
    user = update.effective_user
    q, is_new = ama.submit(text, f"telegram:{user.id}", user.full_name)
    engine.xp.award(f"telegram:{user.id}", 3, "ama_submit", user.full_name)
    if is_new:
        await update.effective_message.reply_text(
            f"✅ Added to the AMA queue! Others can upvote it below. 👇\n\n"
            f"❓ <i>{html_escape(q.text)}</i>",
            parse_mode="HTML", reply_markup=_ama_button(q.qid, q.votes),
        )
    else:
        await update.effective_message.reply_text(
            f"👍 Someone already asked something similar — I've added your vote to it "
            f"(now {q.votes}):\n\n❓ <i>{html_escape(q.text)}</i>",
            parse_mode="HTML", reply_markup=_ama_button(q.qid, q.votes),
        )


async def amaopen_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    engine = _engine(context)
    topic = " ".join(context.args).strip()
    engine.ama.open_session(topic)
    announce = (
        "📢 <b>AMA time!</b> " + (f"Topic: <b>{html_escape(topic)}</b>. " if topic else "")
        + "Submit your questions for the team with:\n<code>/ama your question</code>\n"
        "Then upvote the ones you most want answered. Top-voted questions get answered first!"
    )
    # Broadcast to known groups (copy — handlers mutate the set concurrently).
    sent = 0
    for chat_id in list(getattr(_adapter(context), "known_chats", ())):
        try:
            await context.bot.send_message(chat_id, announce, parse_mode="HTML")
            sent += 1
        except Exception:  # noqa: BLE001
            pass
    await update.effective_message.reply_text(
        f"✅ AMA collection OPEN{f' — {topic}' if topic else ''}. Announced to {sent} group(s). "
        "Close it with /amaclose, see submissions with /amalist."
    )


async def amaclose_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    engine = _engine(context)
    engine.ama.close_session()
    ranked = engine.ama.ranked(15)
    if not ranked:
        await update.effective_message.reply_text("✅ AMA closed. No questions were submitted.")
        return
    lines = ["✅ <b>AMA closed — ranked questions</b>"]
    for i, q in enumerate(ranked, 1):
        lines.append(f"\n<b>{i}. ({q.votes} 👍)</b> {html_escape(q.text)}"
                     f"\n<i>— {html_escape(q.submitter_name or 'member')}</i>")
    await update.effective_message.reply_text("\n".join(lines)[:4096], parse_mode="HTML")


async def amalist_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    ranked = _engine(context).ama.ranked(20)
    if not ranked:
        await update.effective_message.reply_text("No AMA questions yet. Open one with /amaopen.")
        return
    lines = ["🎤 <b>AMA questions (by votes)</b>"]
    for i, q in enumerate(ranked, 1):
        lines.append(f"{i}. ({q.votes} 👍) {html_escape(q.text[:120])}")
    await update.effective_message.reply_text("\n".join(lines)[:4096], parse_mode="HTML")


async def amaclear_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    _engine(context).ama.clear()
    await update.effective_message.reply_text("🧹 AMA queue cleared.")


async def rank_cmd(update, context) -> None:
    """A member's own XP, level, streak, and rank."""
    from ...engagement import level_for

    engine = _engine(context)
    user = update.effective_user
    rec = engine.xp.get(f"telegram:{user.id}")
    if not rec or rec.xp == 0:
        await update.effective_message.reply_text(
            "You're just getting started! 🌱 Ask questions, answer quizzes (/quiz-time in the "
            "group), and keep a daily streak to climb the leaderboard. /leaderboard to see the top."
        )
        return
    _, title = level_for(rec.xp)
    rank = engine.xp.rank(f"telegram:{user.id}")
    await update.effective_message.reply_text(
        f"🏅 <b>{user.first_name}</b> — {title}\n"
        f"XP: <b>{rec.xp}</b> (#{rank}) · this week: {rec.xp_week}\n"
        f"🔥 Streak: {rec.streak} day(s) (best {rec.best_streak})\n\n"
        "Earn XP: helpful questions, quiz wins, referrals, daily activity. /leaderboard",
        parse_mode="HTML",
    )


async def leaderboard_cmd(update, context) -> None:
    from ...engagement import level_for

    engine = _engine(context)
    weekly = engine.xp.top(10, weekly=True)
    board = weekly if weekly else engine.xp.top(10)
    if not board:
        await update.effective_message.reply_text(
            "The leaderboard is wide open — be the first to score. Ask a question or catch the "
            "next quiz! 🏆"
        )
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>This week's top community members</b>"]
    for i, u in enumerate(board):
        tag = medals[i] if i < 3 else f"{i+1}."
        _, title = level_for(u.xp)
        name = u.display_name or u.user_key.split(":")[-1]
        lines.append(f"{tag} <b>{name}</b> — {u.xp_week} XP ({title})")
    lines.append("\nCheck your own standing with /rank.")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def qualify_cmd(update, context) -> None:
    """Start the in-chat fit check (issuer pre-qualifier)."""
    await _adapter(context).start_axis(update.effective_message, update.effective_user)


async def resources_cmd(update, context) -> None:
    """Official resources. `/resources <asset> <jurisdiction>` tailors them."""
    from ...leads import matcher

    args = [a.lower().strip() for a in (context.args or [])]
    if args:
        alias = {"realestate": "real_estate", "re": "real_estate", "property": "real_estate",
                 "pe": "fund", "equity": "equity", "debt": "credit", "rwa": ""}
        asset = alias.get(args[0], args[0])
        juris = args[1] if len(args) > 1 else ""
        text = matcher.match(asset, juris, update.effective_user.first_name or "")
    else:
        text = matcher.resources_overview()
    await update.effective_message.reply_text(
        text, parse_mode="HTML", disable_web_page_preview=True
    )


async def check_cmd(update, context) -> None:
    """Check STBU balances for a public wallet address across eligible chains."""
    addr = context.args[0].strip() if context.args else ""
    if not addr:
        await update.effective_message.reply_text(
            "Send your <b>public</b> wallet address and I'll check your STBU across all "
            "eligible chains:\n<code>/check 0xYourAddress</code>\n\n"
            "🔒 I only read public balances — never share your seed phrase or private key.",
            parse_mode="HTML",
        )
        return
    await update.effective_message.reply_text("🔎 Checking the chains…")
    report = await _engine(context).check_wallet(addr)
    await update.effective_message.reply_text(report, parse_mode="HTML",
                                              disable_web_page_preview=True)


async def price_cmd(update, context) -> None:
    """Live STBU market price / market cap / 24h volume (CoinGecko, CMC fallback).
    A published market FACT — with the 'not advice, not the company valuation' framing."""
    engine = _engine(context)
    snap = await engine.market_snapshot()
    if not snap:
        await update.effective_message.reply_text(
            "I can't reach the STBU market feed right now. You can check it on CoinGecko: "
            "https://www.coingecko.com/en/coins/stobox-token\n\n"
            "This is market data, not investment advice.",
            parse_mode="HTML", disable_web_page_preview=True,
        )
        return
    canon = getattr(engine.assembler, "canonicals", None) if engine.assembler else None
    contracts = (
        canon.get("tokens.stbu.migration.eligible_contracts", {}) if canon else {}
    )
    await update.effective_message.reply_text(
        snap.format_report(contracts=contracts),
        parse_mode="HTML", disable_web_page_preview=True,
    )


async def email_cmd(update, context) -> None:
    """Email the user a full write-up of their last topic: /email you@addr.com."""
    import asyncio as _asyncio

    from ...ops.email import valid_email

    chat = update.effective_chat
    if chat.type != "private":
        await update.effective_message.reply_text(
            "Let's keep your email private — DM me /email you@address.com. 👍"
        )
        return
    if not context.args or not valid_email(context.args[0]):
        await update.effective_message.reply_text("Usage: /email you@address.com")
        return
    engine = _engine(context)
    adapter = _adapter(context)
    addr = context.args[0].strip()
    user = update.effective_user
    uk = f"telegram:{user.id}"

    profile = await engine.memory.get_profile(uk, user.full_name)
    profile.email = addr
    topic = (adapter._email_pending.pop(user.id, None)
             or (profile.recent_questions[-1] if profile.recent_questions else None)
             or "How Stobox tokenization works")
    await engine.memory.save_profile(profile)

    await update.effective_message.reply_text("📩 On it — composing your write-up…")
    resp = await engine.detailed_answer(topic, user_key=uk)
    from ...channels.base import Channel
    body = (
        f"Hi{(' ' + user.first_name) if user.first_name else ''},\n\n"
        f"Here's the fuller answer to: “{topic}”\n\n"
        f"{_strip_html(resp.text)}\n"
        f"{_strip_html(Channel.render_citations(resp))}\n\n"
        "Questions any time — just message Stoby on Telegram.\n"
        "This is information, not investment advice.\n\n— Stoby, the Stobox community AI"
    )
    subject = f"Stobox — {topic[:60]}"
    sent = False
    if engine.email.configured:
        sent = await _asyncio.to_thread(engine.email.send, addr, subject, body)
    # Always capture as a warm lead (email = strong intent).
    engine.leads.update_score(profile, buying_intent=True, has_email=True)
    await engine.leads.handoff(profile)
    await adapter.notify_mql_admins(context, profile)   # Telegram safety net
    await engine.memory.save_profile(profile)

    if sent:
        await update.effective_message.reply_text(
            f"✅ Sent to {addr}. Check your inbox (and spam, just in case). "
            "Anything else you'd like me to dig into?"
        )
    else:
        await update.effective_message.reply_text(
            f"✅ Got it — I've flagged your interest and the Stobox team will email {addr} "
            "the full details. Meanwhile, I'm right here for any questions."
        )


def _strip_html(text: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", text or "")


async def stopreminders_cmd(update, context) -> None:
    removed = _engine(context).reminders.unsubscribe(str(update.effective_chat.id))
    await update.effective_message.reply_text(
        "Done — no more reminders. You can rejoin anytime with /remindme."
        if removed else "You weren't subscribed — nothing to stop. 🙂"
    )


def _subs_markup(chat_id: str, book):
    """Toggle keyboard: one row per topic, ✅/⬜ reflecting current state."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    from ...ops.subscriptions import TOPICS

    active = set(book.topics_for(chat_id))
    rows = [
        [InlineKeyboardButton(
            f"{'✅' if key in active else '⬜'} {meta['label']}",
            callback_data=f"sub:{key}",
        )]
        for key, meta in TOPICS.items()
    ]
    if active:
        rows.append([InlineKeyboardButton("🔕 Turn all off", callback_data="sub:__all_off")])
    return InlineKeyboardMarkup(rows)


def _subs_summary(chat_id: str, book) -> str:
    from ...ops.subscriptions import TOPICS

    active = book.topics_for(chat_id)
    if not active:
        return ("🔔 <b>Topic subscriptions</b>\nYou're not subscribed to anything yet. "
                "Tap a topic below and I'll DM you the moment something in that lane ships "
                "— nothing else. Opt out any time.")
    labels = ", ".join(TOPICS[t]["label"] for t in active if t in TOPICS)
    return (f"🔔 <b>Topic subscriptions</b>\nYou're getting: {labels}.\n"
            "Tap to toggle. I only DM when there's real news, and every message has a way out.")


async def subscribe_cmd(update, context) -> None:
    """Manage opt-in topic subscriptions (DM only). /subscribe [topic] or a menu."""
    chat = update.effective_chat
    if chat.type != "private":
        await update.effective_message.reply_text(
            "Subscriptions are personal — DM me /subscribe and pick your topics. 👍"
        )
        return
    from ...ops.subscriptions import TOPICS, valid_topic

    book = _engine(context).subscriptions
    chat_id = str(chat.id)
    args = context.args or []
    if args:
        topic = args[0].lower().strip().lstrip("#")
        aliases = {"rwa": "rwa-news", "news": "rwa-news", "product-updates": "product",
                   "migrate": "migration"}
        topic = aliases.get(topic, topic)
        if not valid_topic(topic):
            valid = ", ".join(TOPICS)
            await update.effective_message.reply_text(
                f"I don't have a “{args[0]}” topic. Pick one of: {valid}.\n"
                "Or just send /subscribe for the menu."
            )
            return
        added = book.subscribe(chat_id, topic)
        meta = TOPICS[topic]
        head = (f"✅ Subscribed to {meta['label']} — {meta['blurb']}"
                if added else f"You're already on {meta['label']}. 👍")
        await update.effective_message.reply_text(
            head + "\n\nTap to adjust:", reply_markup=_subs_markup(chat_id, book)
        )
        return
    await update.effective_message.reply_text(
        _subs_summary(chat_id, book), parse_mode="HTML",
        reply_markup=_subs_markup(chat_id, book), disable_web_page_preview=True,
    )


async def subscriptions_cmd(update, context) -> None:
    """Show current subscriptions (works in DM)."""
    await subscribe_cmd(update, context)


async def unsubscribe_cmd(update, context) -> None:
    chat = update.effective_chat
    book = _engine(context).subscriptions
    chat_id = str(chat.id)
    args = context.args or []
    from ...ops.subscriptions import TOPICS, valid_topic

    if args:
        topic = args[0].lower().strip().lstrip("#")
        if valid_topic(topic) and book.unsubscribe(chat_id, topic):
            await update.effective_message.reply_text(
                f"Done — you're off {TOPICS[topic]['label']}. Re-join anytime with /subscribe."
            )
            return
    removed = book.unsubscribe_all(chat_id)
    await update.effective_message.reply_text(
        "Done — all topic subscriptions off. You can re-pick anytime with /subscribe."
        if removed else "You weren't subscribed to any topics. 🙂"
    )


async def help_cmd(update, context) -> None:
    await update.effective_message.reply_text(
        "<b>Stoby — commands</b>\n"
        "/guide – interactive tour of what I can do\n"
        "/migrate – STBU→Base migration explainer\n"
        "/check – check your STBU across chains (paste a public address)\n"
        "/compass – Stobox Compass + readiness check\n"
        "/valuation – company valuation (not a token price)\n"
        "/blog – latest posts + the weekly RWA digest\n"
        "/sources – official links to verify me\n"
        "/rank – your XP, level &amp; streak · /leaderboard – top members\n"
        "/ama – submit a question for the next community AMA\n"
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
        "I'm <b>Stoby</b>, the resident AI of the Stobox community — part monster, part mind, "
        "fully awake. I'm grounded in stobox.io's published content and updated automatically "
        "when the site updates. I'm an AI, not a human; official pages and offering documents "
        "always take precedence over me, and I can be wrong.\n\n"
        f"Knowledge last synced: {synced}. I don't give financial or legal advice.",
        parse_mode="HTML", disable_web_page_preview=True,
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
        "Happy to connect you. Holders can reach support at support@stobox.io; if you're "
        "exploring tokenization, the team's right here: https://www.stobox.io/contact. "
        "Or just ask me and I'll point you the right way.",
        parse_mode="HTML", disable_web_page_preview=True,
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


async def cleanup_cmd(update, context) -> None:
    """Remove deleted (ghost) accounts. Reply to one to remove it now; otherwise
    explain the auto-cleanup + Telegram's bulk-listing limit."""
    if not _is_admin(update, context):
        return
    from ...moderation.deleted import is_deleted_account

    adapter = _adapter(context)
    chat = update.effective_chat
    target = _reply_target(update)
    if target is not None:
        if not is_deleted_account(target):
            await update.effective_message.reply_text(
                "That account looks active (it has a name or username), so I won't "
                "remove it. Reply to a *deleted* account's message with /cleanup."
            )
            return
        ok = await adapter._remove_deleted_account(context, chat, target)
        await update.effective_message.reply_text(
            "🧹 Removed a deleted account." if ok else
            "Couldn't remove it — make sure I'm an admin with ban rights here."
        )
        return
    removed = getattr(adapter, "deleted_removed", 0)
    await update.effective_message.reply_text(
        "🧹 <b>Deleted-account cleanup</b>\n"
        f"I auto-remove ghost accounts the moment I see them join or change status "
        f"(removed so far: <b>{removed}</b>). Make sure I'm an <b>admin with ban "
        "rights</b> so I can act.\n\n"
        "To remove one right now, <b>reply to a message from that deleted account</b> "
        "with /cleanup.\n\n"
        "⚠️ Telegram doesn't let bots list every member, so I can't sweep all "
        "existing ghosts in one shot — I clear them as they surface. For a full "
        "one-time purge of old ones, use a Telegram desktop client's member view.",
        parse_mode="HTML",
    )


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
    lines.append(f"deleted accounts removed: {getattr(_adapter(context), 'deleted_removed', 0)}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def appeal_cmd(update, context) -> None:
    """User contests a moderation action → routed to admins."""
    user = update.effective_user
    reason = " ".join(context.args) if context.args else "(no details given)"
    for admin_id in _adapter(context).admins:
        try:
            await context.bot.send_message(
                admin_id,
                f"📣 <b>Appeal</b> from {html_escape(user.full_name)} (id {user.id}):\n"
                f"“{html_escape(reason[:400])}”\n"
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
        lines.append(f"\n<b>#{e.qid}</b> ({e.ask_count}× · {e.created})\n"
                     f"“{html_escape(e.question[:200])}”")
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


async def quiz_cmd(update, context) -> None:
    """Fire a quiz in the current chat now (admin). Correct answers award XP."""
    if not _is_admin(update, context):
        return
    await update.effective_message.reply_text("🧠 Quiz time! Generating…")
    ok = await _adapter(context).send_quiz(context, update.effective_chat.id)
    if not ok:
        await update.effective_message.reply_text("Couldn't build a quiz right now — try /sync first.")


async def pause_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    reason = " ".join(context.args) if context.args else "manual"
    _engine(context).pause(reason)
    await update.effective_message.reply_text(
        f"⏸️ Stoby PAUSED ({reason}). It will answer only with static FAQ + contact info. "
        "Use /resume to restore."
    )


async def resume_cmd(update, context) -> None:
    if not _is_admin(update, context):
        return
    _engine(context).resume()
    await update.effective_message.reply_text("▶️ Stoby RESUMED. Full answering restored.")


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


async def content_cmd(update, context) -> None:
    """Draft blog outlines from community question-gaps. `/content file` files
    them as GitHub issues; bare `/content` previews without filing."""
    if not _is_admin(update, context):
        return
    engine = _engine(context)
    do_file = bool(context.args and context.args[0].lower() in ("file", "go", "issues"))
    decisions = engine.decisions.records()
    results = await engine.flywheel.run(decisions, dry_run=not do_file, limit=5)
    if not results:
        await update.effective_message.reply_text(
            "No fresh content themes yet — need a few more recurring questions. "
            "Check /gaps for what's building."
        )
        return
    lines = [f"📝 <b>Content ideas from community questions</b> ({len(results)}):"]
    for r in results:
        tag = "🕳 gap" if r["is_gap"] else f"{r['count']}×"
        if r["filed"]:
            lines.append(f"✅ #{r['issue']} · {tag} — {r['title'][:70]}")
        else:
            lines.append(f"• {tag} — {r['title'][:70]}")
    if not do_file:
        has_token = bool(engine.flywheel.token)
        lines.append("\n<i>Preview only.</i> " + (
            "Run <code>/content file</code> to open GitHub issues."
            if has_token else "Set GITHUB_TOKEN to file these as issues."))
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


def _fmt_log(entries) -> str:
    from datetime import datetime
    lines = []
    for m in entries:
        try:
            ts = datetime.fromisoformat(m.at).strftime("%m-%d %H:%M")
        except ValueError:
            ts = "?"
        # Logged names/messages are member-controlled: escape, or one stray '<'
        # in a message makes Telegram reject the whole /log reply.
        who = html_escape(m.display_name or (f"@{m.username}" if m.username else m.user_id))
        lines.append(f"[{ts}] <b>{who}</b>: {html_escape(m.text[:160])}")
    return "\n".join(lines)


async def log_cmd(update, context) -> None:
    """Recent messages in this chat from the internal log. /log [N]."""
    if not _is_admin(update, context):
        return
    engine = _engine(context)
    chat_id = str(update.effective_chat.id)
    n = int(context.args[0]) if context.args and context.args[0].isdigit() else 20
    entries = engine.message_log.recent(chat_id, min(n, 50))
    if not entries:
        await update.effective_message.reply_text(
            "No messages logged for this chat yet (or logging is off)."
        )
        return
    total = engine.message_log.total(chat_id)
    await update.effective_message.reply_text(
        f"🗒 <b>Last {len(entries)} of {total} logged messages</b>\n\n{_fmt_log(entries)}",
        parse_mode="HTML", disable_web_page_preview=True,
    )


async def whosaid_cmd(update, context) -> None:
    """Search this chat's log. /whosaid <term>  or  /whosaid @user."""
    if not _is_admin(update, context):
        return
    engine = _engine(context)
    chat_id = str(update.effective_chat.id)
    term = " ".join(context.args).strip() if context.args else ""
    target = _reply_target(update)
    if target:
        entries = engine.message_log.by_user(chat_id, str(target.id))
        label = f"from {html_escape(target.full_name)}"
    elif term.startswith("@"):
        entries = engine.message_log.by_user(chat_id, term)
        label = f"from {html_escape(term)}"
    elif term:
        entries = engine.message_log.search(chat_id, term)
        label = f"matching “{html_escape(term)}”"
    else:
        await update.effective_message.reply_text(
            "Usage: /whosaid <term>, /whosaid @user, or reply to a user with /whosaid."
        )
        return
    if not entries:
        await update.effective_message.reply_text(f"Nothing {label} in the log.")
        return
    await update.effective_message.reply_text(
        f"🔎 <b>Messages {label}</b> ({len(entries)})\n\n{_fmt_log(entries)}",
        parse_mode="HTML", disable_web_page_preview=True,
    )


async def userid_cmd(update, context) -> None:
    """Reply to someone with /userid to get their numeric Telegram ID (for admin
    config). Works on yourself with no reply."""
    if not _is_admin(update, context):
        return
    target = _reply_target(update) or update.effective_user
    uname = f" · @{target.username}" if getattr(target, "username", None) else ""
    await update.effective_message.reply_text(
        f"🆔 <b>{target.full_name}</b>{uname}\nUser ID: <code>{target.id}</code>\n\n"
        "Add to TELEGRAM_ADMIN_USER_IDS to make them an admin.",
        parse_mode="HTML",
    )


async def admin_cmd(update, context) -> None:
    if not _is_admin(update, context):
        await update.effective_message.reply_text("Not authorized.")
        return
    await update.effective_message.reply_text(
        "🔐 Admin: /amaopen /amaclose /amalist /amaclear /quiz /pending /answer /pause "
        "/resume /sync /reindex /stats /health "
        "/digest /faq /gaps /content /prompts /reload /memory /export /logs /debug /test /cache /users\n"
        "🗒 Message log: /log [N] · /whosaid &lt;term|@user&gt; · /userid (reply)\n"
        "🛡 Moderation (reply to a user): /warn /mute [min] /unmute /ban · "
        "/unban &lt;id&gt; /strikes /clearstrikes /cleanup /modstats"
    )


async def _admin_stub(update, context, label: str) -> None:
    if not _is_admin(update, context):
        return
    await update.effective_message.reply_text(f"[{label}] — see /stats and /health for live data.")


def registry() -> dict:
    handlers = {
        "help": help_cmd,
        "start": start_cmd,
        "guide": guide_cmd,
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
        "content": content_cmd,
        "log": log_cmd,
        "whosaid": whosaid_cmd,
        "userid": userid_cmd,
        "whois": userid_cmd,
        "sync": sync_cmd,
        "resync": sync_cmd,
        "pause": pause_cmd,
        "resume": resume_cmd,
        "pending": pending_cmd,
        "answer": answer_cmd,
        "approve": approve_cmd,
        "quiz": quiz_cmd,
        "remindme": remindme_cmd,
        "stopreminders": stopreminders_cmd,
        "subscribe": subscribe_cmd,
        "subscriptions": subscriptions_cmd,
        "mysubs": subscriptions_cmd,
        "unsubscribe": unsubscribe_cmd,
        "email": email_cmd,
        "check": check_cmd,
        "price": price_cmd,
        "stbu": price_cmd,
        "marketcap": price_cmd,
        "mcap": price_cmd,
        "qualify": qualify_cmd,
        "fit": qualify_cmd,
        "readiness": qualify_cmd,
        "resources": resources_cmd,
        "match": resources_cmd,
        "rank": rank_cmd,
        "leaderboard": leaderboard_cmd,
        "top": leaderboard_cmd,
        "ama": ama_cmd,
        "amaopen": amaopen_cmd,
        "amaclose": amaclose_cmd,
        "amalist": amalist_cmd,
        "amaclear": amaclear_cmd,
        # moderation
        "strikes": strikes_cmd,
        "warn": warn_cmd,
        "mute": mute_cmd,
        "unmute": unmute_cmd,
        "ban": ban_cmd,
        "unban": unban_cmd,
        "clearstrikes": clearstrikes_cmd,
        "cleanup": cleanup_cmd,
        "removedeleted": cleanup_cmd,
        "purgedeleted": cleanup_cmd,
        "modstats": modstats_cmd,
        "appeal": appeal_cmd,
        "admin": admin_cmd,
    }
    for topic in _TOPIC_QUERIES:
        handlers[topic] = _make_topic_handler(topic)
    for label in ("export", "logs", "debug", "test", "cache", "users"):
        handlers[label] = (lambda lbl: lambda u, c: _admin_stub(u, c, lbl))(label)
    return handlers
