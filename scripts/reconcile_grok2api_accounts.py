#!/usr/bin/env python3
"""Reconcile grok-register account output files into grok2api.

The script is intentionally conservative: it defaults to dry-run and never
prints SSO tokens. Use --apply to call grok2api /tokens/add.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from account_output import collect_account_records  # noqa: E402


POOL_MAP = {
    "ssobasic": "basic",
    "ssosuper": "super",
    "basic": "basic",
    "super": "super",
    "heavy": "heavy",
}


def find_account_files(register_dir: str | Path) -> list[Path]:
    root = Path(register_dir)
    files = [*root.glob("accounts_*.txt"), *root.glob("accounts_*.jsonl")]
    suffix_order = {".txt": 0, ".jsonl": 1}
    return sorted(files, key=lambda p: (p.stem, suffix_order.get(p.suffix, 9), p.name))


def load_config(register_dir: str | Path) -> dict:
    config_path = Path(register_dir) / "config.json"
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def pool_from_config(config: dict, override: str | None = None) -> str:
    raw = override or config.get("grok2api_pool_name") or "basic"
    return POOL_MAP.get(str(raw).strip().lower(), "basic")


def _post_json(url: str, payload: dict, timeout: int) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:300]}") from exc


def add_tokens_remote(
    base: str,
    app_key: str,
    pool: str,
    tokens: list[str],
    timeout: int = 30,
    *,
    auto_nsfw: bool = False,
) -> dict:
    query_params = {"app_key": app_key}
    if auto_nsfw:
        query_params["auto_nsfw"] = "true"
    query = urllib.parse.urlencode(query_params)
    url = f"{base.rstrip('/')}/tokens/add?{query}"
    payload = {"tokens": tokens, "pool": pool, "tags": ["auto-register", "reconciled"]}
    return _post_json(url, payload, timeout)


def chunked(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile grok-register outputs into grok2api.")
    parser.add_argument("--register-dir", default=str(PROJECT_ROOT))
    parser.add_argument("--base", default="")
    parser.add_argument("--app-key", default="")
    parser.add_argument("--pool", default="")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--auto-nsfw", action="store_true", help="Ask grok2api to enable NSFW after import.")
    parser.add_argument("--no-auto-nsfw", action="store_true", help="Disable auto NSFW even if config enables it.")
    parser.add_argument("--apply", action="store_true", help="Write parsed tokens to grok2api.")
    args = parser.parse_args(argv)

    register_dir = Path(args.register_dir)
    config = load_config(register_dir)
    base = (args.base or config.get("grok2api_remote_base") or "").strip().rstrip("/")
    app_key = (args.app_key or config.get("grok2api_remote_app_key") or "").strip()
    pool = pool_from_config(config, args.pool or None)
    auto_nsfw = bool(config.get("enable_nsfw", False))
    if args.auto_nsfw:
        auto_nsfw = True
    if args.no_auto_nsfw:
        auto_nsfw = False

    files = find_account_files(register_dir)
    records = collect_account_records(files)
    tokens = [record["sso"] for record in records]

    print(f"register_dir={register_dir}")
    print(f"files={len(files)}")
    print(f"unique_records={len(records)}")
    print(f"pool={pool}")
    print(f"auto_nsfw={str(auto_nsfw).lower()}")

    if not args.apply:
        print("mode=dry-run")
        print("hint=pass --apply to write missing tokens through grok2api /tokens/add")
        return 0

    if not base or not app_key:
        print("error=missing grok2api base or app key", file=sys.stderr)
        return 2

    added = 0
    skipped = 0
    for batch in chunked(tokens, max(1, args.batch_size)):
        result = add_tokens_remote(base, app_key, pool, batch, timeout=args.timeout, auto_nsfw=auto_nsfw)
        added += int(result.get("count") or 0)
        skipped += int(result.get("skipped") or 0)

    print("mode=apply")
    print(f"added={added}")
    print(f"skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
