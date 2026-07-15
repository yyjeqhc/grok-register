#!/usr/bin/env python3
"""Probe HTTP proxies: exit IP (ipify) + optional site reachability.

Example:
  python3 scripts/probe_proxies.py \\
    --file /root/empty/hotmail/all_proxies.txt \\
    --alive /root/empty/hotmail/alive_proxies.txt \\
    --site https://sf4.yyjeqhc.cn/mail-api/
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

# Allow running from repo root or scripts/
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proxy_pool import load_proxy_file, proxy_log_label  # noqa: E402


def _probe_one(
    proxy: str,
    *,
    timeout: float,
    site: str,
) -> dict:
    try:
        import requests
    except ImportError as e:  # pragma: no cover
        return {"proxy": proxy, "ok": False, "error": f"import requests: {e}"}

    u = urlparse(proxy)
    out: dict = {
        "proxy": proxy,
        "host": u.hostname,
        "port": u.port,
        "user": u.username,
    }
    proxies = {"http": proxy, "https": proxy}

    t0 = time.time()
    try:
        r = requests.get("https://api.ipify.org", proxies=proxies, timeout=timeout)
        body = (r.text or "").strip()[:80]
        out["ipify"] = {
            "ok": r.status_code == 200 and bool(body),
            "status": r.status_code,
            "ms": int((time.time() - t0) * 1000),
            "ip": body,
        }
    except Exception as e:
        out["ipify"] = {
            "ok": False,
            "error": f"{type(e).__name__}: {e}"[:200],
            "ms": int((time.time() - t0) * 1000),
        }

    if site:
        t1 = time.time()
        try:
            r2 = requests.get(site, proxies=proxies, timeout=timeout, allow_redirects=True)
            out["site"] = {
                "ok": r2.status_code < 500,
                "status": r2.status_code,
                "ms": int((time.time() - t1) * 1000),
            }
        except Exception as e:
            out["site"] = {
                "ok": False,
                "error": f"{type(e).__name__}: {e}"[:200],
                "ms": int((time.time() - t1) * 1000),
            }

    out["ok"] = bool(out.get("ipify", {}).get("ok"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Probe proxy list connectivity")
    ap.add_argument("--file", required=True, help="Input proxy list (one URL per line)")
    ap.add_argument("--alive", default="", help="Write alive proxies here")
    ap.add_argument("--dead", default="", help="Write dead proxies here")
    ap.add_argument("--summary", default="", help="Write JSON summary here")
    ap.add_argument("--site", default="https://sf4.yyjeqhc.cn/mail-api/", help="Extra URL to probe")
    ap.add_argument("--workers", type=int, default=40)
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--limit", type=int, default=0, help="Only first N proxies (0=all)")
    args = ap.parse_args(argv)

    proxies = load_proxy_file(args.file)
    if args.limit and args.limit > 0:
        proxies = proxies[: args.limit]
    if not proxies:
        print(f"no proxies in {args.file}", file=sys.stderr)
        return 2

    print(f"loaded {len(proxies)} from {args.file}", flush=True)
    ok_list: list[dict] = []
    fail_list: list[dict] = []
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = [
            ex.submit(_probe_one, p, timeout=args.timeout, site=args.site) for p in proxies
        ]
        for i, fut in enumerate(concurrent.futures.as_completed(futs), 1):
            r = fut.result()
            (ok_list if r.get("ok") else fail_list).append(r)
            if i % 50 == 0 or i == len(proxies):
                print(
                    f"progress {i}/{len(proxies)} ok={len(ok_list)} fail={len(fail_list)}",
                    flush=True,
                )

    order = {p: i for i, p in enumerate(proxies)}
    ok_list.sort(key=lambda x: order.get(x["proxy"], 10**9))
    fail_list.sort(key=lambda x: order.get(x["proxy"], 10**9))

    if args.alive:
        Path(args.alive).write_text(
            "\n".join(x["proxy"] for x in ok_list) + ("\n" if ok_list else ""),
            encoding="utf-8",
        )
    if args.dead:
        Path(args.dead).write_text(
            "\n".join(x["proxy"] for x in fail_list) + ("\n" if fail_list else ""),
            encoding="utf-8",
        )

    site_ok = sum(1 for x in ok_list if x.get("site", {}).get("ok"))
    summary = {
        "total": len(proxies),
        "ipify_ok": len(ok_list),
        "ipify_fail": len(fail_list),
        "site": args.site,
        "site_ok_among_alive": site_ok,
        "elapsed_sec": round(time.time() - t0, 1),
        "sample_alive": [
            {
                "label": proxy_log_label(x["proxy"]),
                "exit_ip": x.get("ipify", {}).get("ip"),
                "site": x.get("site"),
            }
            for x in ok_list[:10]
        ],
        "sample_fail": [
            {
                "label": proxy_log_label(x["proxy"]),
                "err": x.get("ipify", {}).get("error") or x.get("ipify"),
            }
            for x in fail_list[:10]
        ],
    }
    if args.summary:
        Path(args.summary).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if ok_list else 1


if __name__ == "__main__":
    raise SystemExit(main())
