"""Register-machine hook: mint CPA xai auth after successful registration.

Mint strategy (config ``cpa_mint_mode``):

- ``sso_first`` (default for strong-welfare pipeline): HTTP OAuth via SSO cookie
  (``xconsole_client``), fall back to device-code browser mint on failure.
- ``sso_only``: SSO protocol only (no device browser).
- ``device_only``: legacy device-code browser mint only.

Strong welfare: chat probe is the gate for hotload / live. Mint files always go
to ``cpa_auth_dir`` (staging); ``cpa_hotload_dir`` only receives chat-OK files
when ``cpa_require_chat`` is true (default true when probe_chat is on).

OIDC device package: ``./cpa_xai``.
SSO protocol package: ``./xconsole_client`` (vendored from sso_to_cliproxy_tool).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

_REG_DIR = Path(__file__).resolve().parent
_DEFAULT_OUT = _REG_DIR / "cpa_auths"
_DEFAULT_CPA = Path("")  # empty = do not assume a machine-local CPA path

# Align with cpa_xai.schema.DEFAULT_CLIENT_HEADERS (CLIProxy free Build path)
_SHELL_HEADERS = {
    "x-grok-client-version": "0.2.93",
    "x-xai-token-auth": "xai-grok-cli",
    "x-authenticateresponse": "authenticate-response",
    "x-grok-client-identifier": "grok-shell",
    "User-Agent": "grok-shell/0.2.93 (linux; x86_64)",
}


def _ensure_project_on_path() -> None:
    if str(_REG_DIR) not in sys.path:
        sys.path.insert(0, str(_REG_DIR))


def _ensure_cpa_xai_on_path(tools_dir: str | Path | None = None) -> Path:
    """Put the parent of `cpa_xai` on sys.path. Default: this project root."""
    if tools_dir:
        tools = Path(tools_dir).expanduser().resolve()
    else:
        env = (os.environ.get("API_REVERSE_TOOLS") or "").strip()
        tools = Path(env).expanduser().resolve() if env else _REG_DIR
    if tools.name == "cpa_xai" and (tools / "__init__.py").is_file():
        tools = tools.parent
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    return tools


def export_cookies_from_page(page: Any) -> list[dict]:
    """Best-effort export of cookies from a DrissionPage tab/browser."""
    if page is None:
        return []
    cookies = None
    for getter in (
        lambda: page.cookies(all_domains=True, all_info=True),
        lambda: page.cookies(all_domains=True),
        lambda: page.cookies(),
    ):
        try:
            cookies = getter()
            if cookies:
                break
        except TypeError:
            continue
        except Exception:
            continue
    if not cookies:
        try:
            browser = getattr(page, "browser", None)
            if browser is not None:
                cookies = browser.cookies()
        except Exception:
            cookies = None
    if isinstance(cookies, list):
        return [c for c in cookies if isinstance(c, dict)]
    return []


def _resolve_proxy(cfg: dict) -> str:
    """Same sticky proxy as browser register when cpa_proxy is empty.

    Priority (via proxy_pool): cpa_proxy override → thread pin → proxy → env.
    """
    try:
        from proxy_pool import resolve_active_proxy

        return resolve_active_proxy(cfg, for_mint=True)
    except Exception:
        pass
    proxy = (cfg.get("cpa_proxy") or cfg.get("proxy") or "").strip()
    if proxy:
        return proxy
    return (
        os.environ.get("https_proxy")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("http_proxy")
        or ""
    ).strip()


def _extract_sso(sso: str | None, cookies: Any) -> str:
    sso_val = (sso or "").strip()
    if sso_val:
        return sso_val
    if isinstance(cookies, list):
        for c in cookies:
            if isinstance(c, dict) and c.get("name") in ("sso", "sso-rw") and c.get("value"):
                return str(c.get("value")).strip()
    return ""


def _align_shell_headers(path: Path) -> dict[str, Any]:
    """Normalize CPA auth JSON headers to grok-shell free-path defaults."""
    data = json.loads(path.read_text(encoding="utf-8"))
    headers = dict(data.get("headers") or {})
    headers.update(_SHELL_HEADERS)
    data["headers"] = headers
    if not str(data.get("base_url") or "").rstrip("/").endswith("/v1"):
        bu = str(data.get("base_url") or "").rstrip("/")
        if bu.endswith("cli-chat-proxy.grok.com"):
            data["base_url"] = bu + "/v1"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return data


def _probe_chat(
    access_token: str,
    *,
    base_url: str,
    proxy: str | None,
    log: Callable[[str], None],
) -> dict[str, Any]:
    _ensure_project_on_path()
    from cpa_xai.probe import probe_mini_response  # type: ignore

    pr = probe_mini_response(access_token, base_url=base_url, proxy=proxy or None)
    log(
        f"chat probe: ok={pr.get('ok')} status={pr.get('status')} "
        f"err={(pr.get('error') or '')[:120]!r}"
    )
    return pr


def _maybe_hotload(
    result: dict[str, Any],
    cfg: dict,
    cpa_dir: Path | None,
    log: Callable[[str], None],
) -> None:
    """Copy staging auth to hotload only when policy allows (strong welfare)."""
    if not result.get("mint_ok") or not result.get("path"):
        return
    if not cfg.get("cpa_copy_to_hotload", False) or not cpa_dir:
        return

    require_chat = bool(cfg.get("cpa_require_chat", cfg.get("cpa_probe_chat", False)))
    if require_chat and not result.get("chat_ok"):
        log("[cpa] skip hotload: chat not OK (strong welfare gate)")
        result["hotload_skipped"] = "chat_required"
        return

    try:
        cpa_dir.mkdir(parents=True, exist_ok=True)
        src = Path(result["path"])
        dst = cpa_dir / src.name
        shutil.copy2(src, dst)
        os.chmod(dst, 0o600)
        result["cpa_path"] = str(dst)
        log(f"[cpa] hotload copy -> {dst}")
    except Exception as e:  # noqa: BLE001
        log(f"[cpa] hotload copy failed: {e}")
        result["cpa_copy_error"] = str(e)


def _finalize_result_ok(result: dict[str, Any], cfg: dict) -> dict[str, Any]:
    """Set overall ``ok`` under strong-welfare policy."""
    require_chat = bool(cfg.get("cpa_require_chat", False))
    if not result.get("mint_ok"):
        result["ok"] = False
        return result
    if require_chat:
        result["ok"] = bool(result.get("chat_ok"))
        if not result["ok"] and not result.get("error"):
            result["error"] = "mint ok but chat probe failed (strong welfare)"
    else:
        result["ok"] = True
    return result


def mint_via_sso(
    email: str,
    password: str,
    sso: str,
    *,
    auth_dir: Path,
    base_url: str,
    proxy: str = "",
    yescaptcha_key: str = "",
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """SSO cookie → OAuth protocol mint → xai-*.json under auth_dir."""
    log = log or (lambda _m: None)
    _ensure_project_on_path()
    try:
        from xconsole_client.oauth_protocol import login_with_protocol
    except Exception as e:  # noqa: BLE001
        return {"mint_ok": False, "error": f"import xconsole_client: {e}", "method": "sso"}

    email = (email or "").strip() or "unknown@local"
    password = password or ""
    sso = (sso or "").strip()
    if not sso:
        return {"mint_ok": False, "error": "missing sso", "method": "sso"}

    auth_dir.mkdir(parents=True, exist_ok=True)
    log(f"sso mint start: {email} -> {auth_dir}")
    try:
        oauth = login_with_protocol(
            email,
            password,
            yescaptcha_key=(yescaptcha_key or "").strip() or "dummy",
            proxy=proxy or "",
            debug=False,
            cliproxyapi_auth_dir=str(auth_dir),
            cliproxyapi_base_url=base_url,
            session_cookies={"sso": sso},
        )
    except Exception as e:  # noqa: BLE001
        log(f"sso mint failed: {e}")
        return {"mint_ok": False, "error": str(e), "method": "sso"}

    path = Path(oauth.cliproxyapi_path) if oauth.cliproxyapi_path else None
    if path is None or not path.is_file():
        # Fallback: newest json in auth_dir
        cands = sorted(auth_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        path = cands[0] if cands else None
    if path is None or not path.is_file():
        return {"mint_ok": False, "error": "sso mint produced no auth file", "method": "sso"}

    data = _align_shell_headers(path)
    access = str(data.get("access_token") or oauth.access_token or "")
    log(f"sso mint wrote {path}")
    return {
        "mint_ok": True,
        "method": "sso",
        "path": str(path),
        "access_token": access,
        "email": str(data.get("email") or email),
        "base_url": str(data.get("base_url") or base_url),
    }


def mint_via_device(
    email: str,
    password: str,
    *,
    auth_dir: Path,
    page: Any | None,
    cookies: Any | None,
    sso: str,
    cfg: dict,
    proxy: str,
    base_url: str,
    log: Callable[[str], None],
    cancel: Callable[[], bool] | None,
) -> dict[str, Any]:
    """Legacy device-code browser mint (cpa_xai)."""
    tools_dir = cfg.get("api_reverse_tools") or cfg.get("cpa_xai_parent") or None
    _ensure_cpa_xai_on_path(tools_dir)
    try:
        from cpa_xai import mint_and_export  # type: ignore
    except Exception as e:  # noqa: BLE001
        return {"mint_ok": False, "error": f"import cpa_xai: {e}", "method": "device"}

    headless = bool(cfg.get("cpa_headless", False))
    probe = bool(cfg.get("cpa_probe_after_write", True))
    # Device path internal models probe; chat handled at export layer for welfare.
    probe_chat = False
    timeout = float(cfg.get("cpa_mint_timeout_sec", 240))
    force_standalone = bool(cfg.get("cpa_force_standalone", True))
    reuse_browser = bool(cfg.get("cpa_mint_browser_reuse", True))
    recycle_every = int(cfg.get("cpa_mint_browser_recycle_every", 15) or 0)
    cookie_inject = bool(cfg.get("cpa_mint_cookie_inject", True))

    use_cookies = cookies
    if not cookie_inject:
        use_cookies = None
    else:
        sso_val = (sso or "").strip()
        if sso_val:
            base = list(use_cookies) if isinstance(use_cookies, list) else []
            for name in ("sso", "sso-rw"):
                for dom in (".x.ai", "accounts.x.ai", ".accounts.x.ai", "auth.x.ai", ".auth.x.ai"):
                    base.append(
                        {
                            "name": name,
                            "value": sso_val,
                            "domain": dom,
                            "path": "/",
                            "secure": True,
                            "httpOnly": True,
                        }
                    )
            use_cookies = base

    log(
        f"device mint start: {email} cookies="
        f"{len(use_cookies) if isinstance(use_cookies, list) else 0} reuse={reuse_browser}"
    )

    def _log(msg: str) -> None:
        log(msg)

    result = mint_and_export(
        email=email,
        password=password,
        auth_dir=auth_dir,
        page=None if force_standalone else page,
        proxy=proxy or None,
        headless=headless,
        base_url=base_url,
        probe=probe,
        probe_chat=probe_chat,
        browser_timeout_sec=timeout,
        force_standalone=force_standalone,
        cookies=use_cookies,
        reuse_browser=reuse_browser,
        recycle_every=recycle_every,
        log=_log,
        cancel=cancel,
    )
    if not result.get("ok"):
        return {
            "mint_ok": False,
            "error": result.get("error") or "device mint failed",
            "method": "device",
            "raw": result,
        }

    path = Path(result["path"])
    access = ""
    try:
        data = _align_shell_headers(path)
        access = str(data.get("access_token") or "")
    except Exception:
        pass
    return {
        "mint_ok": True,
        "method": "device",
        "path": str(path),
        "access_token": access,
        "email": email,
        "base_url": base_url,
        "probe_models": result.get("probe_models"),
    }


def export_cpa_xai_for_account(
    email: str,
    password: str,
    *,
    page: Any | None = None,
    cookies: Any | None = None,
    sso: str | None = None,
    config: dict | None = None,
    log_callback: Callable[[str], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> dict:
    """Mint OIDC + write xai-<email>.json; optional chat-gated hotload."""
    cfg = config or {}
    log = log_callback or (lambda m: print(m, flush=True))

    if not cfg.get("cpa_export_enabled", False):
        log("[cpa] export disabled")
        return {"ok": False, "skipped": True, "reason": "disabled"}

    out_dir = Path(cfg.get("cpa_auth_dir") or _DEFAULT_OUT).expanduser()
    if not out_dir.is_absolute():
        out_dir = (_REG_DIR / out_dir).resolve()

    hotload_raw = (cfg.get("cpa_hotload_dir") or "").strip()
    cpa_dir = Path(hotload_raw).expanduser() if hotload_raw else None
    if cpa_dir and not cpa_dir.is_absolute():
        cpa_dir = (_REG_DIR / cpa_dir).resolve()

    proxy = _resolve_proxy(cfg)
    base_url = (cfg.get("cpa_base_url") or "https://cli-chat-proxy.grok.com/v1").rstrip("/")
    if not base_url.endswith("/v1"):
        if base_url.endswith("cli-chat-proxy.grok.com"):
            base_url = base_url + "/v1"

    mode = str(cfg.get("cpa_mint_mode") or "sso_first").strip().lower()
    if mode not in ("sso_first", "sso_only", "device_only"):
        mode = "sso_first"
    device_fallback = bool(cfg.get("cpa_device_fallback", True))
    # Strong welfare defaults: probe chat when require_chat is on
    require_chat = bool(cfg.get("cpa_require_chat", False))
    probe_chat = bool(cfg.get("cpa_probe_chat", require_chat))

    cookie_inject = bool(cfg.get("cpa_mint_cookie_inject", True))
    use_cookies = cookies
    if use_cookies is None and cookie_inject and page is not None:
        use_cookies = export_cookies_from_page(page)
    sso_val = _extract_sso(sso, use_cookies)

    out_dir.mkdir(parents=True, exist_ok=True)
    log(
        f"[cpa] mint mode={mode} device_fallback={device_fallback} "
        f"require_chat={require_chat} probe_chat={probe_chat} "
        f"email={email} has_sso={bool(sso_val)} -> {out_dir}"
    )

    def _log(msg: str) -> None:
        log(f"[cpa] {msg}")

    result: dict[str, Any] = {
        "ok": False,
        "mint_ok": False,
        "chat_ok": False,
        "method": None,
        "email": email,
    }
    tried: list[str] = []

    # --- SSO path ---
    if mode in ("sso_first", "sso_only"):
        if not sso_val:
            _log("sso path skipped: no sso cookie")
            if mode == "sso_only":
                result["error"] = "sso_only but missing sso"
                return _finalize_result_ok(result, cfg)
        else:
            tried.append("sso")
            sso_res = mint_via_sso(
                email,
                password,
                sso_val,
                auth_dir=out_dir,
                base_url=base_url,
                proxy=proxy,
                yescaptcha_key=str(cfg.get("yescaptcha_api_key") or cfg.get("yescaptcha_key") or ""),
                log=_log,
            )
            result.update({k: v for k, v in sso_res.items() if k != "raw"})
            if sso_res.get("mint_ok"):
                if probe_chat:
                    pr = _probe_chat(
                        sso_res.get("access_token") or "",
                        base_url=base_url,
                        proxy=proxy or None,
                        log=_log,
                    )
                    result["chat_ok"] = bool(pr.get("ok"))
                    result["probe_chat"] = pr
                    if not result["chat_ok"]:
                        result["error"] = f"chat probe failed: {pr.get('error') or pr.get('status')}"
                _finalize_result_ok(result, cfg)
                _maybe_hotload(result, cfg, cpa_dir, log)
                if result.get("mint_ok") and (result.get("ok") or not require_chat):
                    return result
                # mint ok but chat failed under require_chat: still try device? usually no —
                # chat is account entitlement; device won't help. Return staging path.
                if result.get("mint_ok") and require_chat and not result.get("chat_ok"):
                    log("[cpa] sso mint ok but chat failed; skip device (entitlement)")
                    return result
            else:
                _log(f"sso mint error: {sso_res.get('error')}")
                result["error"] = sso_res.get("error")
                if mode == "sso_only" or not device_fallback:
                    return _finalize_result_ok(result, cfg)

    # --- Device fallback / primary ---
    if mode == "device_only" or (
        mode == "sso_first" and device_fallback and not result.get("mint_ok")
    ):
        tried.append("device")
        dev_res = mint_via_device(
            email,
            password,
            auth_dir=out_dir,
            page=page,
            cookies=use_cookies,
            sso=sso_val,
            cfg=cfg,
            proxy=proxy,
            base_url=base_url,
            log=_log,
            cancel=cancel_callback,
        )
        result.update({k: v for k, v in dev_res.items() if k != "raw"})
        if dev_res.get("mint_ok"):
            if probe_chat:
                pr = _probe_chat(
                    dev_res.get("access_token") or "",
                    base_url=base_url,
                    proxy=proxy or None,
                    log=_log,
                )
                result["chat_ok"] = bool(pr.get("ok"))
                result["probe_chat"] = pr
                if not result["chat_ok"]:
                    result["error"] = f"chat probe failed: {pr.get('error') or pr.get('status')}"
        else:
            result["error"] = dev_res.get("error") or result.get("error") or "device mint failed"

    result["tried"] = tried
    _finalize_result_ok(result, cfg)
    _maybe_hotload(result, cfg, cpa_dir, log)

    if not result.get("mint_ok"):
        fail_path = out_dir / "cpa_auth_failed.txt"
        with open(fail_path, "a", encoding="utf-8") as f:
            f.write(
                f"{email}----{result.get('error') or 'unknown'}----"
                f"{result.get('method') or '-'}----{int(time.time())}\n"
            )
        if cfg.get("cpa_mint_required", False):
            raise RuntimeError(f"CPA mint required but failed: {result.get('error')}")

    return result
