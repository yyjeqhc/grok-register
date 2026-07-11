"""Parse legacy and JSONL account outputs from the register machine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class AccountLine:
    email: str
    password: str
    sso: str
    raw: str
    line_no: int


def parse_accounts_file(path: str | Path) -> list[AccountLine]:
    path = Path(path)
    out: list[AccountLine] = []
    if not path.is_file():
        return out
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split("----")
        if len(parts) < 2:
            continue
        email = parts[0].strip()
        password = parts[1].strip()
        sso = parts[2].strip() if len(parts) > 2 else ""
        if not email or not password:
            continue
        out.append(AccountLine(email=email, password=password, sso=sso, raw=s, line_no=i))
    return out


def parse_account_files(paths: list[str | Path]) -> list[AccountLine]:
    """Read current ``accounts_*.txt/jsonl`` files and deduplicate by email."""
    from account_output import iter_account_records

    by_email: dict[str, AccountLine] = {}
    for record in iter_account_records(paths):
        email = str(record.get("email") or "").strip()
        password = str(record.get("password") or "")
        sso = str(record.get("sso") or "").strip()
        if not email or not password or not sso:
            continue
        by_email[email.lower()] = AccountLine(
            email=email,
            password=password,
            sso=sso,
            raw="",
            line_no=int(record.get("source_line") or 0),
        )
    return list(by_email.values())


def existing_cpa_emails(auth_dir: str | Path) -> set[str]:
    """Emails already present as xai-*.json in auth_dir."""
    auth_dir = Path(auth_dir)
    found: set[str] = set()
    if not auth_dir.is_dir():
        return found
    for p in auth_dir.glob("xai-*.json"):
        name = p.name[len("xai-") : -len(".json")]
        if name:
            found.add(name.lower())
        try:
            import json

            d = json.loads(p.read_text(encoding="utf-8"))
            em = str(d.get("email") or "").strip().lower()
            if em:
                found.add(em)
        except Exception:
            continue
    return found
