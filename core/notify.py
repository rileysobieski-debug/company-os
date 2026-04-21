"""
Notification layer
==================
Delivers reports, approvals, and alerts to Riley via Telegram and/or email,
with triage logic so Riley doesn't receive the same thing on every channel.

Principles:
  1. Vault-first: every notification's authoritative artifact is a file in the
     company folder. Channels carry a heads-up pointer, not the full content.
  2. One kind-urgency pair → one channel (or two for urgent), never all three.
  3. Quiet hours (22:00-07:00 ET) suppress non-urgent sends; they queue for
     next morning's digest.
  4. Per-kind cooldown prevents repetitive sends.
  5. De-dup log at `.company-os-state/notify-log.jsonl` (outside vault) — last
     N hashes of sent messages checked before send.

Credentials:
  Read from `~/.company-os/.env` (outside the Obsidian vault so secrets
  don't sync). Envars available:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
    EMAIL_SMTP_HOST
    EMAIL_SMTP_PORT
    EMAIL_SMTP_USER
    EMAIL_SMTP_APP_PASSWORD
    EMAIL_FROM
    EMAIL_TO_BUSINESS
    EMAIL_TO_PERSONAL

Public:
  notify(kind, urgency, title, body, vault_path=None, links=None) -> NotifyResult
"""

from __future__ import annotations

import hashlib
import json
import os
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Iterable, Literal
from urllib import request as urlrequest


# ---------------------------------------------------------------------------
# Environment / config
# ---------------------------------------------------------------------------
ENV_PATH = Path.home() / ".company-os" / ".env"
STATE_DIR = Path.home() / ".company-os" / "state"
LOG_PATH = STATE_DIR / "notify-log.jsonl"
LOG_RETAIN = 500  # last N entries kept after a trim
LOG_TRIM_TRIGGER = LOG_RETAIN * 2  # trim when file grows past this many lines


def _load_env() -> dict[str, str]:
    """Effective env for notify: .env file contents overridden by os.environ
    when set. Delegates parsing to core.env.read_env_file so the .env
    grammar lives in one place."""
    from core.env import read_env_file
    out = read_env_file(ENV_PATH)
    for key in list(out.keys()):
        env_val = os.environ.get(key)
        if env_val:
            out[key] = env_val
    return out


# ---------------------------------------------------------------------------
# Triage matrix
# ---------------------------------------------------------------------------
Kind = Literal[
    "digest",             # end-of-session summary
    "approval_request",   # something is in pending-approval/
    "decision_required",  # Riley's decision gates the next step
    "report",             # a finished artifact Riley should see
    "error",              # pipeline error worth surfacing
    "info",               # low-importance update
]

Urgency = Literal["low", "normal", "high"]


@dataclass(frozen=True)
class Route:
    telegram: bool
    email_business: bool
    email_personal: bool


# (kind, urgency) → routing
# Philosophy:
#   - High urgency + decision gating → telegram + business email (two channels, same content).
#   - Normal urgency reports → business email only (searchable, archivable).
#   - Low info → vault only (no channel send at all).
#   - Digests → business email (end-of-session summary is a read, not an alert).
_TRIAGE: dict[tuple[Kind, Urgency], Route] = {
    ("digest", "low"):             Route(False, False, False),
    ("digest", "normal"):          Route(False, True,  False),
    ("digest", "high"):            Route(True,  True,  False),

    ("approval_request", "low"):   Route(False, False, False),
    ("approval_request", "normal"): Route(False, True, False),
    ("approval_request", "high"):  Route(True,  True,  False),

    ("decision_required", "low"):  Route(False, True,  False),
    ("decision_required", "normal"): Route(True, True, False),
    ("decision_required", "high"): Route(True,  True,  True),

    ("report", "low"):             Route(False, False, False),
    ("report", "normal"):          Route(False, True,  False),
    ("report", "high"):            Route(True,  True,  False),

    ("error", "low"):              Route(False, True,  False),
    ("error", "normal"):           Route(True,  True,  False),
    ("error", "high"):             Route(True,  True,  True),

    ("info", "low"):               Route(False, False, False),
    ("info", "normal"):            Route(False, False, False),
    ("info", "high"):              Route(False, True,  False),
}


def route_for(kind: Kind, urgency: Urgency) -> Route:
    return _TRIAGE.get((kind, urgency), Route(False, False, False))


# ---------------------------------------------------------------------------
# Quiet hours (UTC-aware)
# ---------------------------------------------------------------------------
# The intended local quiet window is 22:00–07:00 Eastern Time. Because ET
# shifts between UTC-5 (standard) and UTC-4 (daylight), a fixed UTC window
# can only approximate the intent. The window below spans 02:00–12:00 UTC,
# which covers both:
#   - Summer (ET-4):  22:00 ET = 02:00 UTC,  08:00 ET = 12:00 UTC
#   - Winter (ET-5):  22:00 ET = 03:00 UTC,  07:00 ET = 12:00 UTC
# The resulting ten-hour window is one hour wider than the original nine-hour
# local window; this extra hour is acceptable slack for a quiet-hours
# heuristic that errs on the side of suppression.
#
# TODO Phase 3+: switch to zoneinfo.ZoneInfo("America/New_York") so the
# window follows the user's local DST boundary exactly.
QUIET_START = time(2, 0)    # 02:00 UTC ≈ 22:00 ET (summer) / 21:00 ET (winter)
QUIET_END = time(12, 0)     # 12:00 UTC ≈ 08:00 ET (summer) / 07:00 ET (winter)


def _is_quiet_now(now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    t = now.time()
    # Quiet window wraps midnight
    if QUIET_START <= QUIET_END:
        return QUIET_START <= t < QUIET_END
    return t >= QUIET_START or t < QUIET_END


# ---------------------------------------------------------------------------
# De-dup log
# ---------------------------------------------------------------------------
def _message_hash(kind: Kind, title: str, body: str) -> str:
    h = hashlib.sha256()
    h.update(kind.encode("utf-8"))
    h.update(b"\n")
    h.update(title.encode("utf-8"))
    h.update(b"\n")
    h.update(body.encode("utf-8"))
    return h.hexdigest()[:32]


def _load_log() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    out = []
    for raw in LOG_PATH.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


# Byte-size threshold for the fast-path check. Entries average ~250B; this
# gives us a loose upper bound for "file has probably grown past
# LOG_TRIM_TRIGGER lines". A false positive just means an extra trim-check
# that turns out to be a no-op — harmless. A false negative can't happen
# because real entries never fall below ~100B.
_LOG_TRIM_BYTES = LOG_TRIM_TRIGGER * 100


def _trim_log_to_retain() -> None:
    """Rewrite the log keeping only the last LOG_RETAIN entries."""
    existing = _load_log()
    if len(existing) <= LOG_RETAIN:
        return
    tail = existing[-LOG_RETAIN:]
    LOG_PATH.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in tail) + "\n",
        encoding="utf-8",
    )


def _append_log(entry: dict) -> None:
    """Append a single JSON line in O(1). A rewrite happens only when the
    file's byte size crosses a threshold that approximates
    LOG_TRIM_TRIGGER entries — so the amortized cost per append is O(1)
    and we avoid re-reading the full log on every notification."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False))
        fh.write("\n")
    # Fast path: stat is O(1). Only escalate to a full load+trim when the
    # file is clearly oversize.
    try:
        size = LOG_PATH.stat().st_size
    except OSError:
        return
    if size > _LOG_TRIM_BYTES:
        _trim_log_to_retain()


def _recently_sent(msg_hash: str, lookback: int = 50) -> bool:
    for entry in _load_log()[-lookback:]:
        if entry.get("hash") == msg_hash:
            return True
    return False


# ---------------------------------------------------------------------------
# Channel implementations
# ---------------------------------------------------------------------------
def _send_telegram(env: dict[str, str], title: str, body: str, links: Iterable[str]) -> tuple[bool, str]:
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False, "missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"

    text_parts = [f"*{title}*", "", body.strip()]
    link_list = [l for l in links if l]
    if link_list:
        text_parts.append("")
        text_parts.append("Links:")
        for l in link_list:
            text_parts.append(f"• {l}")
    text = "\n".join(text_parts)[:4000]  # Telegram hard cap ~4096

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    req = urlrequest.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlrequest.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                return True, "ok"
            return False, f"telegram http {resp.status}"
    except Exception as exc:  # noqa: BLE001
        return False, f"telegram error: {exc}"


def _send_email(env: dict[str, str], to: str, title: str, body: str, links: Iterable[str]) -> tuple[bool, str]:
    host = env.get("EMAIL_SMTP_HOST")
    port = int(env.get("EMAIL_SMTP_PORT", "587"))
    user = env.get("EMAIL_SMTP_USER")
    pw = env.get("EMAIL_SMTP_APP_PASSWORD")
    sender = env.get("EMAIL_FROM", user or "")
    if not (host and user and pw and sender):
        return False, "missing SMTP credentials"
    if not to:
        return False, "no recipient"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = title
    msg["From"] = sender
    msg["To"] = to

    link_block = ""
    link_list = [l for l in links if l]
    if link_list:
        link_block = "\n\nLinks:\n" + "\n".join(f"- {l}" for l in link_list)
    plain = f"{body.strip()}{link_block}"

    msg.attach(MIMEText(plain, "plain", "utf-8"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(user, pw)
            server.sendmail(sender, [to], msg.as_string())
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, f"smtp error: {exc}"


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------
@dataclass
class NotifyResult:
    kind: str
    urgency: str
    title: str
    route_used: Route
    telegram_ok: bool | None = None
    email_business_ok: bool | None = None
    email_personal_ok: bool | None = None
    notes: list[str] = field(default_factory=list)
    suppressed_for_quiet: bool = False
    suppressed_for_dup: bool = False


def notify(
    kind: Kind,
    urgency: Urgency,
    title: str,
    body: str,
    vault_path: Path | None = None,
    links: Iterable[str] | None = None,
    force: bool = False,
) -> NotifyResult:
    """Send a notification through triage.

    Parameters
    ----------
    kind, urgency : routing keys (see _TRIAGE)
    title : short subject / headline
    body : message text (plain; Telegram renders light Markdown)
    vault_path : path to the authoritative artifact inside the vault; added
                 to links so Riley can jump straight to the file
    links : additional links (URLs or file paths)
    force : bypass quiet hours and de-dup (use for user-requested sends)
    """
    env = _load_env()
    route = route_for(kind, urgency)
    result = NotifyResult(kind=str(kind), urgency=str(urgency), title=title, route_used=route)

    all_links: list[str] = []
    if vault_path is not None:
        all_links.append(f"vault://{vault_path}")
    if links:
        all_links.extend(str(l) for l in links)

    # No channel selected — vault-only
    if not (route.telegram or route.email_business or route.email_personal):
        result.notes.append("vault-only (no channel for this kind+urgency)")
        return result

    # Quiet hours
    if not force and _is_quiet_now() and urgency != "high":
        result.suppressed_for_quiet = True
        result.notes.append("suppressed by quiet hours (22:00-07:00)")
        return result

    # De-dup
    msg_hash = _message_hash(kind, title, body)
    if not force and _recently_sent(msg_hash):
        result.suppressed_for_dup = True
        result.notes.append("suppressed as duplicate (identical msg sent recently)")
        return result

    # Send
    if route.telegram:
        ok, note = _send_telegram(env, title, body, all_links)
        result.telegram_ok = ok
        result.notes.append(f"telegram: {note}")
    if route.email_business:
        ok, note = _send_email(env, env.get("EMAIL_TO_BUSINESS", ""), title, body, all_links)
        result.email_business_ok = ok
        result.notes.append(f"email_business: {note}")
    if route.email_personal:
        ok, note = _send_email(env, env.get("EMAIL_TO_PERSONAL", ""), title, body, all_links)
        result.email_personal_ok = ok
        result.notes.append(f"email_personal: {note}")

    _append_log(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "hash": msg_hash,
            "kind": kind,
            "urgency": urgency,
            "title": title,
            "telegram": result.telegram_ok,
            "email_business": result.email_business_ok,
            "email_personal": result.email_personal_ok,
        }
    )
    return result
