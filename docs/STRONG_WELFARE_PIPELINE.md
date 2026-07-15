# Strong-welfare pipeline (register + SSO mint + chat gate)

## Goal

Only accounts that can **chat** on Grok Build (`cli-chat-proxy`) count as welfare.  
Mint success alone is not enough.

## Flow

```text
grok-register (remote)
  → register → SSO
  → grok2api (optional remote add)
  → CPA mint:
       1) SSO protocol (xconsole_client)  [fast, no 2nd browser]
       2) device-code (cpa_xai)          [fallback]
  → staging: cpa_auth_dir / xai-*.json
  → chat probe (/v1/responses)
  → only chat OK → cpa_hotload_dir (live CPA)
```

## Config (recommended)

```json
{
  "cpa_export_enabled": true,
  "cpa_mint_mode": "sso_first",
  "cpa_device_fallback": true,
  "cpa_probe_chat": true,
  "cpa_require_chat": true,
  "cpa_auth_dir": "./cpa_auths",
  "cpa_copy_to_hotload": true,
  "cpa_hotload_dir": "/path/to/cpa-stack/auths",
  "cpa_base_url": "https://cli-chat-proxy.grok.com/v1"
}
```

| Key | Meaning |
| --- | --- |
| `cpa_mint_mode` | `sso_first` / `sso_only` / `device_only` |
| `cpa_device_fallback` | On SSO fail, try device browser mint |
| `cpa_require_chat` | Overall `ok` only if chat probe passes |
| `cpa_copy_to_hotload` | Copy to live only when chat OK (if require_chat) |
| `proxy_file` | Optional proxy list (one `http://user:pass@ip:port` per line) |
| `proxy_pool_mode` | `rotate` / `random` / `off` |
| `proxy` | Static single proxy fallback when pool empty |
| `cpa_proxy` | Mint-only override; **leave empty** so SSO mint uses the same sticky proxy as browser |

### Sticky proxy (register + SSO mint)

Each account picks one proxy from `proxy_file` and pins it on the worker thread for:

1. Chromium signup  
2. HTTP helpers (mail API via `get_proxies()`, NSFW, …)  
3. SSO protocol CPA mint + chat probe  

Probe first, then point `proxy_file` at the alive list:

```bash
python3 scripts/probe_proxies.py \
  --file /path/to/all_proxies.txt \
  --alive /path/to/alive_proxies.txt \
  --dead /path/to/dead_proxies.txt \
  --summary /path/to/proxy_probe_summary.json \
  --site https://sf4.yyjeqhc.cn/mail-api/
```

## Deploy remote

1. Sync this repo (includes vendored `xconsole_client/` + `cpa_xai/`).
2. `pip install -r requirements.txt` (needs `curl_cffi`, `requests`, DrissionPage).
3. Copy `config.example.json` → `config.json` and set mail + grok2api + hotload paths.
4. Enable `cpa_export_enabled` when ready.

## Clean pools (this server)

Backup first (already under `sso_to_cliproxy_tool/account_data/backups/`), then:

```bash
python3 scripts/clean_welfare_pools.py          # dry-run
python3 scripts/clean_welfare_pools.py --yes    # archive live + empty grok2api + empty live
```

Hotmail is **out of scope** until this pipeline is stable.
