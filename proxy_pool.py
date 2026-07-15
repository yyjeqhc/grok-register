"""Per-account sticky outbound proxy for register browser + SSO/CPA mint.

Design goals:
  - One proxy URL is pinned on the current thread for the whole account pipeline
    (browser signup → HTTP helpers → SSO protocol mint → chat probe).
  - Optional pool file (one URL per line) rotates/random-picks per account.
  - ``config.proxy`` remains the static single-proxy fallback.
  - ``config.cpa_proxy`` still overrides mint-only when set (leave empty to keep sticky).

Thread-local pin avoids cross-talk if multiple workers ever share a process.
"""

from __future__ import annotations

import os
import random
import threading
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

LogFn = Callable[[str], None]

_thread = threading.local()
_pool_lock = threading.Lock()
_pool: dict[str, Any] = {
    "path": "",
    "proxies": [],  # list[str]
    "index": 0,
    "dead": set(),  # set[str]
    "mode": "rotate",  # rotate | random | off
}


def set_runtime_proxy(proxy: str | None) -> None:
    """Pin proxy for the *current thread*. Empty clears pin."""
    p = (proxy or "").strip()
    _thread.proxy = p or None


def get_runtime_proxy() -> str | None:
    return getattr(_thread, "proxy", None)


def clear_runtime_proxy() -> None:
    _thread.proxy = None


def proxy_log_label(proxy: str) -> str:
    """Redact userinfo for logs."""
    p = (proxy or "").strip()
    if not p:
        return ""
    try:
        u = urlparse(p if "://" in p else f"http://{p}")
        host = u.hostname or "?"
        port = u.port or ""
        auth = "user:***@" if u.username else ""
        scheme = u.scheme or "http"
        return f"{scheme}://{auth}{host}{(':' + str(port)) if port else ''}"
    except Exception:
        return "(proxy)"


def normalize_proxy_url(raw: str) -> str:
    p = (raw or "").strip()
    if not p or p.startswith("#"):
        return ""
    if "://" not in p:
        p = "http://" + p
    return p


def load_proxy_file(path: str | Path) -> list[str]:
    """Load proxy URLs from a text file (one per line, # comments ok)."""
    p = Path(path).expanduser()
    if not p.is_file():
        return []
    text = p.read_text(encoding="utf-8", errors="ignore")
    out: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        url = normalize_proxy_url(line)
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def configure_pool(
    *,
    path: str = "",
    mode: str = "rotate",
    proxies: Iterable[str] | None = None,
    force_reset: bool = False,
) -> int:
    """Install/replace the global pool. Returns number of usable proxies.

    If the path and proxy list are unchanged, keep rotate index and dead set
    so consecutive ``acquire_proxy_for_account`` calls actually rotate.
    """
    mode_n = (mode or "rotate").strip().lower()
    if mode_n not in ("rotate", "random", "off"):
        mode_n = "rotate"
    if proxies is None:
        items = load_proxy_file(path) if path else []
    else:
        items = []
        seen: set[str] = set()
        for raw in proxies:
            url = normalize_proxy_url(str(raw))
            if url and url not in seen:
                seen.add(url)
                items.append(url)
    path_s = str(path or "")
    with _pool_lock:
        same = (
            not force_reset
            and _pool.get("path") == path_s
            and _pool.get("proxies") == items
        )
        _pool["path"] = path_s
        _pool["proxies"] = items
        _pool["mode"] = mode_n
        if not same:
            _pool["index"] = 0
            _pool["dead"] = set()
        else:
            # Drop dead entries that no longer exist in the file.
            dead = _pool.get("dead") or set()
            _pool["dead"] = {d for d in dead if d in set(items)}
    return len(items)


def configure_pool_from_config(cfg: dict | None) -> int:
    """Read ``proxy_file`` / ``proxy_pool_mode`` from register config."""
    cfg = cfg or {}
    mode = str(cfg.get("proxy_pool_mode") or "rotate").strip().lower()
    path = str(cfg.get("proxy_file") or "").strip()
    if not path:
        # No pool file → empty pool; static config.proxy still works via resolve.
        with _pool_lock:
            _pool["path"] = ""
            _pool["proxies"] = []
            _pool["index"] = 0
            _pool["mode"] = mode if mode in ("rotate", "random", "off") else "off"
        return 0
    # Resolve relative to grok-register root when not absolute.
    p = Path(path).expanduser()
    if not p.is_absolute():
        root = Path(__file__).resolve().parent
        cand = (root / p).resolve()
        if cand.is_file():
            p = cand
        else:
            # Also try path as-is relative to CWD.
            p = Path(path).expanduser()
    return configure_pool(path=str(p), mode=mode)


def pool_size() -> int:
    with _pool_lock:
        return len(_pool["proxies"])


def pool_alive_count() -> int:
    with _pool_lock:
        dead = _pool["dead"]
        return sum(1 for x in _pool["proxies"] if x not in dead)


def mark_proxy_dead(proxy: str) -> None:
    p = normalize_proxy_url(proxy)
    if not p:
        return
    with _pool_lock:
        _pool["dead"].add(p)


def _pick_from_pool_locked() -> str:
    proxies: list[str] = _pool["proxies"]
    dead: set[str] = _pool["dead"]
    mode: str = _pool["mode"]
    if mode == "off" or not proxies:
        return ""
    alive = [x for x in proxies if x not in dead]
    if not alive:
        # All marked dead — reset and try full list again.
        dead.clear()
        alive = list(proxies)
    if not alive:
        return ""
    if mode == "random":
        return random.choice(alive)
    # rotate
    n = len(proxies)
    start = int(_pool["index"]) % n
    for offset in range(n):
        cand = proxies[(start + offset) % n]
        if cand not in dead:
            _pool["index"] = (start + offset + 1) % n
            return cand
    return alive[0]


def pick_pool_proxy() -> str:
    with _pool_lock:
        return _pick_from_pool_locked()


def resolve_active_proxy(cfg: dict | None = None, *, for_mint: bool = False) -> str:
    """Resolve the outbound proxy for HTTP/browser/mint.

    Priority:
      1. thread-local pin (sticky per account) — skipped for mint only when
         ``cpa_proxy`` is explicitly set (intentional mint override).
      2. for_mint: ``cpa_proxy`` if non-empty
      3. thread-local pin
      4. ``config.proxy``
      5. env HTTPS_PROXY / HTTP_PROXY
    """
    cfg = cfg or {}
    if for_mint:
        cpa = str(cfg.get("cpa_proxy") or "").strip()
        if cpa:
            return normalize_proxy_url(cpa) or cpa

    pin = (get_runtime_proxy() or "").strip()
    if pin:
        return pin

    static = str(cfg.get("proxy") or "").strip()
    if static:
        return normalize_proxy_url(static) or static

    for key in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY"):
        env = (os.environ.get(key) or "").strip()
        if env:
            return normalize_proxy_url(env) or env
    return ""


def acquire_proxy_for_account(
    cfg: dict | None = None,
    *,
    log: LogFn | None = None,
    rotate: bool = True,
) -> str:
    """Pick + pin a proxy for one account pipeline.

    - If pool file has entries and mode != off: pick next/random and pin.
    - Else pin static ``config.proxy`` (may be empty = direct).
    - Returns the pinned proxy URL (empty string = direct / no proxy).
    - ``rotate=False`` reuses the existing thread pin when present.
    """
    cfg = cfg or {}
    if not rotate:
        cur = (get_runtime_proxy() or "").strip()
        if cur:
            return cur

    n = configure_pool_from_config(cfg)
    mode = str(cfg.get("proxy_pool_mode") or "rotate").strip().lower()
    picked = ""
    if mode != "off" and n > 0:
        picked = pick_pool_proxy()
        if log and picked:
            log(
                f"[*] 代理池选取 {proxy_log_label(picked)} "
                f"(pool={pool_alive_count()}/{pool_size()} mode={mode})"
            )
    if not picked:
        picked = str(cfg.get("proxy") or "").strip()
        if picked:
            picked = normalize_proxy_url(picked) or picked
            if log:
                log(f"[*] 使用静态代理: {proxy_log_label(picked)}")
        elif log:
            log("[*] 本账号无代理（直连）")

    set_runtime_proxy(picked or None)
    return picked or ""


def clear_pin_for_direct(log: LogFn | None = None) -> None:
    """Browser fell back to direct — clear pin so SSO mint matches."""
    if get_runtime_proxy():
        if log:
            log("[*] 已清除线程代理钉扎（与浏览器直连对齐）")
        clear_runtime_proxy()
