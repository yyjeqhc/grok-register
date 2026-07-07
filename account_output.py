import datetime
import json
import re
from pathlib import Path


JWT_RE = re.compile(r"eyJ[A-Za-z0-9._-]+")
ACCOUNT_FORMAT = "grok-register-account-v1"


def normalize_sso_token(raw_token):
    token = str(raw_token or "").strip()
    if token.startswith("sso="):
        token = token[4:]
    return token


def account_jsonl_path(accounts_output_file):
    path = Path(accounts_output_file)
    return path.with_suffix(".jsonl")


def format_legacy_account_line(email, password, sso):
    return f"{email}----{password}----{sso}\n"


def format_account_jsonl_line(email, password, sso, created_at=None):
    payload = {
        "format": ACCOUNT_FORMAT,
        "email": str(email or ""),
        "password": str(password or ""),
        "sso": normalize_sso_token(sso),
        "created_at": created_at or datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def append_account_record(accounts_output_file, email, password, sso, created_at=None):
    txt_path = Path(accounts_output_file)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    token = normalize_sso_token(sso)
    with txt_path.open("a", encoding="utf-8") as f:
        f.write(format_legacy_account_line(email, password, token))
    with account_jsonl_path(txt_path).open("a", encoding="utf-8") as f:
        f.write(format_account_jsonl_line(email, password, token, created_at=created_at))


def parse_account_line(line):
    text = str(line or "").strip()
    if not text:
        return None
    if text.startswith("{"):
        return _parse_json_account_line(text)
    return _parse_legacy_account_line(text)


def _parse_json_account_line(text):
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    token = normalize_sso_token(payload.get("sso") or payload.get("token"))
    if not token:
        return None
    return {
        "email": str(payload.get("email") or ""),
        "password": str(payload.get("password") or ""),
        "sso": token,
        "source_format": "jsonl",
    }


def _parse_legacy_account_line(text):
    if "----" not in text:
        return None
    email, rest = text.split("----", 1)
    match = JWT_RE.search(rest)
    if not match:
        return None
    password_part = rest[: match.start()]
    if password_part.endswith("----"):
        password = password_part[:-4]
    else:
        password = password_part
    token = normalize_sso_token(match.group(0))
    return {
        "email": email.strip(),
        "password": password,
        "sso": token,
        "source_format": "legacy",
    }


def iter_account_records(paths):
    for path in paths:
        p = Path(path)
        if not p.exists() or not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, 1):
            record = parse_account_line(line)
            if not record:
                continue
            record["source_file"] = str(p)
            record["source_line"] = line_number
            yield record


def collect_account_records(paths):
    records = []
    seen = set()
    for record in iter_account_records(paths):
        token = record["sso"]
        if token in seen:
            continue
        seen.add(token)
        records.append(record)
    return records
