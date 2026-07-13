#!/usr/bin/env python3
"""Archive + empty grok2api / CPA live pools for a clean strong-welfare start.

Does NOT delete the backup tarball created earlier. Requires --yes to apply.

Examples:
  # dry-run
  python3 scripts/clean_welfare_pools.py

  # apply
  python3 scripts/clean_welfare_pools.py --yes \\
    --grok2api-db /root/empty/grok2api/data/accounts.db \\
    --cpa-live /home/yyjeqhc/cpa-stack/auths
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import time
from pathlib import Path


def archive_dir(src: Path, archive_root: Path) -> Path | None:
    if not src.is_dir():
        return None
    files = list(src.glob("xai-*.json"))
    if not files:
        return None
    dest = archive_root / f"cpa_live_{time.strftime('%Y%m%dT%H%M%SZ')}"
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy2(f, dest / f.name)
    return dest


def clear_cpa_live(src: Path, *, apply: bool) -> int:
    files = list(src.glob("xai-*.json")) if src.is_dir() else []
    if not apply:
        return len(files)
    for f in files:
        f.unlink()
    return len(files)


def clear_grok2api(db: Path, *, apply: bool) -> int:
    if not db.is_file():
        return 0
    con = sqlite3.connect(str(db))
    try:
        n = con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        if apply:
            con.execute("DELETE FROM accounts")
            con.commit()
        return int(n)
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--yes", action="store_true", help="Actually delete/clear")
    ap.add_argument(
        "--grok2api-db",
        default="/root/empty/grok2api/data/accounts.db",
    )
    ap.add_argument(
        "--cpa-live",
        default="/home/yyjeqhc/cpa-stack/auths",
    )
    ap.add_argument(
        "--archive-root",
        default="/root/empty/sso_to_cliproxy_tool/account_data/backups/welfare_archive",
    )
    ap.add_argument(
        "--also-clear-cpa-ok",
        action="store_true",
        help="Also empty account_data/cpa/ok (default: keep as archive reference)",
    )
    ap.add_argument(
        "--cpa-ok",
        default="/root/empty/sso_to_cliproxy_tool/account_data/cpa/ok",
    )
    args = ap.parse_args()

    apply = bool(args.yes)
    archive_root = Path(args.archive_root)
    archive_root.mkdir(parents=True, exist_ok=True)

    g2 = Path(args.grok2api_db)
    live = Path(args.cpa_live)
    ok_dir = Path(args.cpa_ok)

    print(f"mode={'APPLY' if apply else 'DRY-RUN'}")
    g2n = clear_grok2api(g2, apply=False)
    print(f"grok2api accounts: {g2n} @ {g2}")

    live_n = len(list(live.glob('xai-*.json'))) if live.is_dir() else 0
    print(f"cpa live xai-*.json: {live_n} @ {live}")

    ok_n = len(list(ok_dir.glob('xai-*.json'))) if ok_dir.is_dir() else 0
    print(f"cpa ok (reference): {ok_n} @ {ok_dir}")

    if not apply:
        print("\nRe-run with --yes to archive live + empty grok2api + empty live.")
        print("cpa/ok is kept unless --also-clear-cpa-ok.")
        return 0

    # archive live first
    archived = archive_dir(live, archive_root)
    if archived:
        print(f"archived live -> {archived}")
    cleared_live = clear_cpa_live(live, apply=True)
    print(f"cleared cpa live: {cleared_live}")

    cleared_g2 = clear_grok2api(g2, apply=True)
    print(f"cleared grok2api accounts: {cleared_g2}")

    if args.also_clear_cpa_ok and ok_dir.is_dir():
        arch_ok = archive_dir(ok_dir, archive_root)
        if arch_ok:
            print(f"archived ok -> {arch_ok}")
        for f in ok_dir.glob("xai-*.json"):
            f.unlink()
        print("cleared cpa/ok")

    print("done. New registrations are the welfare pool.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
