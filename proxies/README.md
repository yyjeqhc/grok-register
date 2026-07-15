# Proxy pool

Put one HTTP proxy URL per line, for example:

```text
http://user:pass@1.2.3.4:3129
```

Default filename expected by `config.example.json`:

- `proxies/alive_proxies.txt`

Probe a raw list and write an alive file:

```bash
python scripts/probe_proxies.py ^
  --file path\to\all_proxies.txt ^
  --alive proxies\alive_proxies.txt ^
  --dead proxies\dead_proxies.txt ^
  --site https://sf4.yyjeqhc.cn/mail-api/
```

`*.txt` under this folder is gitignored (credentials). Keep the list only on your machine.
