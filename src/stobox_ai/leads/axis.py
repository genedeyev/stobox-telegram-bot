"""In-chat pre-qualifier — a quick 5-question fit check for issuers.

This is a LIGHT indicator, NOT the real AXIS readiness score. It qualifies a
prospect conversationally (asset, jurisdiction, stage, size, timeline), gives a
rough fit signal, and routes them to the real free Readiness Score at
stobox.io/compass while capturing a warm, scored lead. It never fabricates an
AXIS result or promises acceptance/pricing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

READINESS_URL = "https://www.stobox.io/compass"
APP_URL = "https://app.stobox.io"
CONTACT_URL = "https://www.stobox.io/contact"


@dataclass(slots=True)
class Question:
    key: str
    prompt: str
    options: list[tuple[str, str, int]]   # (button label, stored value, fit points)


# Info-only questions score 0; stage/size/timeline drive the fit signal.
QUESTIONS: list[Question] = [
    Question("asset", "🏢 First up — what are you looking to tokenize?", [
        ("Real estate", "real_estate", 0),
        ("Fund / private equity", "fund", 0),
        ("Company equity", "equity", 0),
        ("Private credit / debt", "credit", 0),
    ]),
    Question("jurisdiction", "🌍 Where's the asset or entity based?", [
        ("US", "us", 0), ("EU", "eu", 0), ("UK", "uk", 0), ("Other", "other", 0),
    ]),
    Question("stage", "📈 What stage are you at?", [
        ("Just exploring", "exploring", 0),
        ("Have the asset", "have_asset", 1),
        ("Ready to raise", "ready", 3),
        ("Already raising", "raising", 2),
    ]),
    Question("size", "💰 Rough size of the raise / asset?", [
        ("Under $1M", "lt1m", 0),
        ("$1–10M", "1_10m", 2),
        ("$10–50M", "10_50m", 3),
        ("$50M+", "gt50m", 3),
    ]),
    Question("timeline", "⏱ Timeline to tokenize?", [
        ("This quarter", "q", 3),
        ("3–6 months", "3_6m", 2),
        ("6–12 months", "6_12m", 1),
        ("Just exploring", "exploring", 0),
    ]),
]

_ASSET_LABEL = {"real_estate": "real estate", "fund": "a fund / PE vehicle",
                "equity": "company equity", "credit": "private credit"}


@dataclass
class Session:
    step: int = 0
    score: int = 0
    answers: dict[str, str] = field(default_factory=dict)

    def record(self, q: Question, option_idx: int) -> None:
        _, value, pts = q.options[option_idx]
        self.answers[q.key] = value
        self.score += pts
        self.step += 1

    @property
    def done(self) -> bool:
        return self.step >= len(QUESTIONS)


def band(score: int) -> str:
    if score >= 7:
        return "strong"
    if score >= 4:
        return "promising"
    return "early"


def result_text(session: Session, first_name: str = "") -> str:
    a = session.answers
    asset = _ASSET_LABEL.get(a.get("asset", ""), "your asset")
    b = band(session.score)
    hi = {
        "strong": (
            f"Honestly{',' + ' ' + first_name if first_name else ''} — this looks like a "
            f"<b>strong fit</b>. Tokenizing {asset} at your stage and size is squarely what "
            "Stobox Compass is built for."
        ),
        "promising": (
            f"Nice — this looks <b>promising</b>. There's a real path to tokenizing {asset} "
            "with Stobox; a few specifics will sharpen it."
        ),
        "early": (
            "Good starting point. You're a little <b>earlier</b> in the journey, which is "
            f"totally fine — the best first move is to see where {asset} stands."
        ),
    }[b]
    cta = (
        "\n\n<b>Where to go next</b>\n"
        f"• 📊 <b>See where you stand</b> — the free <b>Readiness Score</b> (25 Q, no "
        f"card, same methodology as Compass): {READINESS_URL}\n"
        f"• 🚀 <b>Ready to start</b> — create your account: {APP_URL}\n"
        f"• 📬 <b>Talk to the team</b> — the contact form: {CONTACT_URL}\n\n"
        "Prefer I pass your details to the team directly? Share your email with "
        "<code>/email you@address.com</code>."
    )
    return f"{hi}{cta}\n\n<i>This is information, not investment advice.</i>"
