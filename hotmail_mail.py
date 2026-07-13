"""Hotmail / Outlook OAuth mail pool for grok-register.

Line format (same as K12 / common card dumps)::

    email----password----client_id----refresh_token

Uses Microsoft consumer token endpoint + IMAP XOAUTH2 (outlook.office365.com).
No browser required for reading xAI verification codes.
"""

from __future__ import annotations

import email as email_lib
import imaplib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from pathlib import Path
from typing import Any, Callable

import requests

TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
IMAP_HOST = "outlook.office365.com"
IMAP_PORT = 993

_lock = threading.RLock()
# email -> active account (claimed for current registration)
_active: dict[str, "HotmailAccount"] = {}


@dataclass
class HotmailAccount:
    email: str
    password: str
    client_id: str
    refresh_token: str
    line_raw: str = ""
    source_file: str = ""
    access_token: str = ""
    access_expires_at: float = 0.0
    claimed_at: float = field(default_factory=time.time)

    @property
    def login_hint(self) -> str:
        return self.email.strip().lower()


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def resolve_tokens_file(config: dict | None = None) -> Path:
    cfg = config or {}
    raw = (
        (cfg.get("hotmail_tokens_file") or "").strip()
        or os.environ.get("HOTMAIL_TOKENS_FILE", "").strip()
    )
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (_project_root() / p).resolve()
        return p
    # defaults
    for cand in (
        _project_root() / "hotmail" / "alive_sample7.txt",
        _project_root() / "hotmail" / "tokens.txt",
    ):
        if cand.is_file() and cand.stat().st_size > 0:
            return cand
    return _project_root() / "hotmail" / "tokens.txt"


def parse_line(line: str) -> HotmailAccount | None:
    line = (line or "").strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split("----")
    if len(parts) < 4:
        return None
    email_a, password, client_id, refresh = (p.strip() for p in parts[:4])
    if not email_a or "@" not in email_a or not client_id or not refresh:
        return None
    return HotmailAccount(
        email=email_a,
        password=password,
        client_id=client_id,
        refresh_token=refresh,
        line_raw=line,
    )


def _side_path(tokens_file: Path, suffix: str) -> Path:
    return tokens_file.with_name(tokens_file.stem + suffix + tokens_file.suffix)


def _read_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


def pool_count(config: dict | None = None) -> int:
    path = resolve_tokens_file(config)
    n = 0
    for ln in _read_lines(path):
        if parse_line(ln):
            n += 1
    return n


def claim_account(config: dict | None = None, log: Callable[[str], None] | None = None) -> HotmailAccount:
    """Atomically take one account from the pool file."""
    log = log or (lambda _m: None)
    path = resolve_tokens_file(config)
    with _lock:
        lines = _read_lines(path)
        kept: list[str] = []
        claimed: HotmailAccount | None = None
        for ln in lines:
            if claimed is None:
                acc = parse_line(ln)
                if acc:
                    acc.source_file = str(path)
                    claimed = acc
                    continue
            kept.append(ln)
        if claimed is None:
            raise RuntimeError(f"Hotmail 池为空或无法解析: {path}")
        _write_lines(path, kept)
        claimed.claimed_at = time.time()
        _active[claimed.login_hint] = claimed
        log(f"[hotmail] claimed {claimed.email} remaining≈{sum(1 for x in kept if parse_line(x))}")
        return claimed


def release_account(
    email: str,
    *,
    reason: str = "retry",
    config: dict | None = None,
    log: Callable[[str], None] | None = None,
) -> None:
    """Put account back to pool (retry) or side file (dead/used)."""
    log = log or (lambda _m: None)
    key = (email or "").strip().lower()
    with _lock:
        acc = _active.pop(key, None)
        if not acc:
            return
        path = resolve_tokens_file(config)
        line = "----".join(
            [acc.email, acc.password, acc.client_id, acc.refresh_token]
        )
        if reason == "retry":
            lines = _read_lines(path)
            lines.append(line)
            _write_lines(path, lines)
            log(f"[hotmail] released back to pool: {acc.email}")
        else:
            side = _side_path(path, f"_{reason}")
            with open(side, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            log(f"[hotmail] moved to {side.name}: {acc.email}")


def mark_used(email: str, config: dict | None = None, log: Callable[[str], None] | None = None) -> None:
    release_account(email, reason="used", config=config, log=log)


def mark_dead(email: str, config: dict | None = None, log: Callable[[str], None] | None = None) -> None:
    release_account(email, reason="dead", config=config, log=log)


def get_active(email: str) -> HotmailAccount | None:
    return _active.get((email or "").strip().lower())


def refresh_access_token(acc: HotmailAccount, *, force: bool = False) -> str:
    if (
        not force
        and acc.access_token
        and acc.access_expires_at
        and time.time() < acc.access_expires_at - 60
    ):
        return acc.access_token

    last_err = ""
    variants: list[dict[str, str]] = [
        {},
        {
            "scope": "https://outlook.office.com/IMAP.AccessAsUser.All offline_access openid profile"
        },
    ]
    for extra in variants:
        form = {
            "client_id": acc.client_id,
            "grant_type": "refresh_token",
            "refresh_token": acc.refresh_token,
            **extra,
        }
        try:
            resp = requests.post(TOKEN_URL, data=form, timeout=30)
            body = resp.json() if resp.content else {}
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            continue
        if resp.status_code == 200 and body.get("access_token"):
            acc.access_token = str(body["access_token"])
            exp_in = int(body.get("expires_in") or 3600)
            acc.access_expires_at = time.time() + exp_in
            new_rt = str(body.get("refresh_token") or "").strip()
            if new_rt:
                acc.refresh_token = new_rt
            return acc.access_token
        last_err = f"HTTP {resp.status_code}: {body.get('error') or body}"
    raise RuntimeError(f"Hotmail refresh failed for {acc.email}: {last_err}")


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _message_body(msg: email_lib.message.Message) -> str:
    parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype in ("text/plain", "text/html"):
                try:
                    payload = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    parts.append(payload.decode(charset, errors="replace"))
                except Exception:
                    continue
    else:
        try:
            payload = msg.get_payload(decode=True) or b""
            charset = msg.get_content_charset() or "utf-8"
            parts.append(payload.decode(charset, errors="replace"))
        except Exception:
            pass
    text = "\n".join(parts)
    text = re.sub(r"<[^>]+>", " ", text)
    return text


def imap_fetch_recent(
    acc: HotmailAccount,
    *,
    folders: tuple[str, ...] = ("INBOX", "Junk", "Junk Email"),
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Fetch recent messages via IMAP XOAUTH2."""
    access = refresh_access_token(acc)
    user = acc.email

    def authobj(_response: bytes | None) -> bytes:
        return f"user={user}\x01auth=Bearer {access}\x01\x01".encode("utf-8")

    out: list[dict[str, Any]] = []
    M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        typ, _ = M.authenticate("XOAUTH2", authobj)
        if typ != "OK":
            raise RuntimeError(f"IMAP AUTH failed: {typ}")

        # list available folders once
        available: list[str] = []
        try:
            typ, data = M.list()
            if typ == "OK" and data:
                for raw in data:
                    if not raw:
                        continue
                    s = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
                    # last quoted segment is mailbox name
                    m = re.search(r' "([^"]+)"$| ([^\s]+)$', s)
                    if m:
                        available.append(m.group(1) or m.group(2) or "")
        except Exception:
            available = []

        def resolve_folder(name: str) -> str | None:
            if not available:
                return name
            low = name.lower()
            for a in available:
                if a.lower() == low:
                    return a
            if low in ("junk", "junk email"):
                for a in available:
                    al = a.lower()
                    if al in ("junk", "junk email", "spam") or "junk" in al:
                        return a
            return name if name.upper() == "INBOX" else None

        for folder in folders:
            real = resolve_folder(folder)
            if not real:
                continue
            try:
                typ, _ = M.select(f'"{real}"' if " " in real else real, readonly=True)
                if typ != "OK":
                    typ, _ = M.select(real, readonly=True)
                if typ != "OK":
                    continue
            except Exception:
                continue
            typ, data = M.search(None, "ALL")
            if typ != "OK" or not data or not data[0]:
                continue
            ids = data[0].split()
            for mid in ids[-limit:]:
                try:
                    typ, msg_data = M.fetch(mid, "(RFC822)")
                    if typ != "OK" or not msg_data or not msg_data[0]:
                        continue
                    raw = msg_data[0][1]
                    if not isinstance(raw, bytes):
                        continue
                    msg = email_lib.message_from_bytes(raw)
                    subj = _decode_header(msg.get("Subject"))
                    frm = _decode_header(msg.get("From"))
                    date_hdr = msg.get("Date") or ""
                    body = _message_body(msg)
                    # received time best-effort
                    try:
                        from email.utils import parsedate_to_datetime

                        dt = parsedate_to_datetime(date_hdr)
                        ts = dt.timestamp()
                    except Exception:
                        ts = 0.0
                    out.append(
                        {
                            "id": f"{real}:{mid.decode() if isinstance(mid, bytes) else mid}",
                            "folder": real,
                            "subject": subj,
                            "from": frm,
                            "date": date_hdr,
                            "received_at": ts,
                            "body": body,
                        }
                    )
                except Exception:
                    continue
    finally:
        try:
            M.logout()
        except Exception:
            pass

    out.sort(key=lambda m: m.get("received_at") or 0, reverse=True)
    return out


def extract_xai_code(text: str, subject: str = "") -> str | None:
    """Reuse same shapes as grok_register_ttk.extract_verification_code."""
    if subject:
        m = re.search(r"^([A-Z0-9]{3}-[A-Z0-9]{3})\s+xAI", subject, re.IGNORECASE)
        if m:
            return m.group(1)
    m = re.search(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b", text or "", re.IGNORECASE)
    if m:
        # prefer if xai context nearby
        return m.group(1)
    for pattern in (
        r"verification\s+code[:\s]+(\d{4,8})",
        r"your\s+code[:\s]+(\d{4,8})",
        r"confirm(?:ation)?\s+code[:\s]+(\d{4,8})",
    ):
        m = re.search(pattern, text or "", re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def inbox_has_prior_xai_mail(acc: HotmailAccount, *, lookback_limit: int = 25) -> list[dict[str, Any]]:
    """Return recent xAI-related messages if this mailbox was already used for xAI."""
    try:
        messages = imap_fetch_recent(acc, limit=lookback_limit)
    except Exception:
        return []
    hits: list[dict[str, Any]] = []
    for msg in messages:
        blob = f"{msg.get('subject') or ''} {msg.get('from') or ''} {msg.get('body') or ''}".lower()
        subj = str(msg.get("subject") or "")
        if (
            "x.ai" in blob
            or "noreply@x.ai" in blob
            or "xai confirmation" in blob
            or re.search(r"\b[A-Z0-9]{3}-[A-Z0-9]{3}\b", subj, re.I)
            and "xai" in subj.lower()
        ):
            hits.append(msg)
    return hits


def get_email_and_token(
    config: dict | None = None,
    log_callback: Callable[[str], None] | None = None,
    *,
    max_refresh_attempts: int = 12,
) -> tuple[str, str]:
    """Claim pool account. Returns (email, dev_token) where dev_token is hotmail:<email>.

    Skips:
      - dead refresh tokens (invalid_grant)
      - mailboxes that already contain xAI verification mail (burned by others)
        when config hotmail_skip_if_xai_mail is true (default).
    """
    log = log_callback or (lambda _m: None)
    cfg = config or {}
    skip_xai = bool(cfg.get("hotmail_skip_if_xai_mail", True))
    last_err: Exception | None = None
    for attempt in range(1, max_refresh_attempts + 1):
        acc = claim_account(config, log=log)
        try:
            refresh_access_token(acc, force=True)
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"[hotmail] refresh failed ({attempt}/{max_refresh_attempts}), mark dead: {e}")
            mark_dead(acc.email, config=config, log=log)
            continue

        if skip_xai:
            hits = inbox_has_prior_xai_mail(acc)
            if hits:
                sample = (hits[0].get("subject") or "")[:60]
                log(
                    f"[hotmail] 收件箱已有 xAI 邮件({len(hits)}), 判定被占用, 跳过: "
                    f"{acc.email} e.g. {sample!r}"
                )
                # burned = already used for xAI by someone else; do not return to pool
                release_account(acc.email, reason="burned", config=config, log=log)
                last_err = RuntimeError(f"prior xAI mail: {sample}")
                continue

        return acc.email, f"hotmail:{acc.login_hint}"
    raise RuntimeError(
        f"Hotmail 连续 {max_refresh_attempts} 个不可用 (dead/burned): {last_err}"
    )


def get_oai_code(
    dev_token: str,
    email: str,
    *,
    timeout: float = 180,
    poll_interval: float = 5,
    log_callback: Callable[[str], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
    min_timestamp: float | None = None,
    config: dict | None = None,
) -> str:
    """Poll Hotmail IMAP for xAI verification code."""
    log = log_callback or (lambda _m: None)
    acc = get_active(email)
    if not acc and isinstance(dev_token, str) and dev_token.startswith("hotmail:"):
        # should already be active
        acc = get_active(dev_token.split(":", 1)[-1])
    if not acc:
        raise RuntimeError(f"Hotmail 会话丢失（未 claim）: {email}")

    # Only accept mail at/after claim (minus small skew)
    if min_timestamp is None:
        min_timestamp = max(0.0, acc.claimed_at - 30.0)

    deadline = time.time() + timeout
    seen: set[str] = set()
    while time.time() < deadline:
        if cancel_callback and cancel_callback():
            raise RuntimeError("cancelled")
        try:
            messages = imap_fetch_recent(acc, limit=20)
        except Exception as e:
            log(f"[hotmail] IMAP 拉取失败: {e}")
            time.sleep(poll_interval)
            continue

        log(f"[hotmail] 本轮邮件 {len(messages)} 封 (after={time.strftime('%H:%M:%S', time.localtime(min_timestamp))})")
        for msg in messages:
            mid = str(msg.get("id") or "")
            if mid in seen:
                continue
            seen.add(mid)
            ts = float(msg.get("received_at") or 0)
            if ts and ts < min_timestamp:
                continue
            subj = str(msg.get("subject") or "")
            body = str(msg.get("body") or "")
            frm = str(msg.get("from") or "")
            combined = f"{subj}\n{body}\n{frm}"
            # prefer xAI
            if "x.ai" not in combined.lower() and "xai" not in combined.lower() and "grok" not in combined.lower():
                # still try extract; xAI subject format is distinctive
                pass
            code = extract_xai_code(combined, subj)
            if code:
                log(f"[hotmail] 验证码: {code} subject={subj[:60]!r}")
                return code
            if subj:
                log(f"[hotmail] 跳过: {subj[:70]}")

        time.sleep(poll_interval)

    raise RuntimeError(f"在 {timeout}s 内未收到验证码邮件 (hotmail): {email}")
