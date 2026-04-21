# Webapp runbook

One page on how to run the Company OS webapp without it rotting.

## What happened on 2026-04-19

Flask's dev server ran for 22 hours and got into a state where every
company-scoped route (`/c/<slug>/*`) returned HTTP 500 while `/healthz`
continued to return 200. We had no traceback because stderr was lost
to the backgrounded process, and no external watchdog because nothing
was polling.

## Three fixes, in order of how much they matter

### 1. Run under Waitress, not Flask's dev server

Flask's `app.run()` is explicitly not production-safe. Waitress is a
pure-Python WSGI server that handles thread pool recycling, connection
timeouts, and clean shutdown. Drop-in replacement.

```bash
# Old (dev server — rots after ~24h)
python webapp/app.py --host 127.0.0.1 --port 5050

# New (Waitress — production-grade)
python webapp/app.py --host 127.0.0.1 --port 5050 --prod
```

Config knobs:
- `--threads N` — Waitress thread pool (default 8)
- `channel_timeout=120` in code — drops stuck connections after 2 min
- `cleanup_interval=30` — sweeps closed connections every 30s

### 2. Use the deep healthcheck, not `/healthz`

`/healthz` is a liveness probe — "is the port open?" — and nothing else.
For real monitoring, poll `/healthz/deep`. It:

- Exercises `discover_companies`
- Loads each company's config + departments
- Renders the company summary
- Probes the job registry

Returns HTTP 503 with a JSON payload of which specific check failed if
any subsystem is broken. Had we been polling `/healthz/deep` on
2026-04-19, the watchdog would have caught the failure in under 60
seconds.

```bash
curl http://127.0.0.1:5050/healthz/deep
```

Response shape on healthy:
```json
{"ok": true, "total_ms": 42, "checks": [...per-check entries...]}
```

### 3. Run the watchdog

`webapp/watchdog.py` launches the webapp as a subprocess, polls
`/healthz/deep` every 60s, and restarts the webapp if:

- 3 consecutive healthcheck fails, OR
- Uptime exceeds 20 hours (proactive rot prevention)

```bash
python webapp/watchdog.py
```

The watchdog launches the webapp in `--prod` mode by default. Logs to
stdout. Leave it running in a terminal, or register as a Windows
Scheduled Task (see below).

## Logs

Tracebacks + access logs go to `company-os/logs/webapp.log`. Rotates
at 5 MB, keeps 5 backups. If the webapp ever 500s again:

```bash
tail -100 company-os/logs/webapp.log
```

should show the exact stack trace.

## Quick reference

| Scenario | Command |
|---|---|
| Local dev (iterating on code) | `python webapp/app.py` |
| Long-running solo use | `python webapp/watchdog.py` |
| Production-style manual run | `python webapp/app.py --prod` |
| Check health quickly | `curl http://127.0.0.1:5050/healthz/deep \| jq .ok` |
| See recent errors | `tail -100 company-os/logs/webapp.log` |

## Windows Scheduled Task (optional)

To auto-launch the watchdog on login:

1. Open Task Scheduler → Create Task
2. Trigger: At log on of `<your user>`
3. Action: `pythonw.exe`
4. Arguments: `"C:\Users\riley_edejtwi\Obsidian Vault\company-os\webapp\watchdog.py"`
5. Start in: `C:\Users\riley_edejtwi\Obsidian Vault\company-os`
6. Settings → "If the task fails, restart every 1 minute" (belt and suspenders)

`pythonw.exe` runs without a console window, so this is invisible.
Logs still go to `company-os/logs/webapp.log`.

## If the webapp dies anyway

1. `tail -100 company-os/logs/webapp.log` — read the stack trace
2. Check process state: `powershell -Command "Get-NetTCPConnection -LocalPort 5050"`
3. Kill any stale python processes: `powershell -Command "Get-Process python | Where-Object {$_.StartTime -lt (Get-Date).AddHours(-20)} | Stop-Process -Force"`
4. Restart: `python webapp/watchdog.py`

## What's still unsolved

- **No alerting.** The watchdog restarts but doesn't notify anyone. If
  restarts start happening every 30s (e.g. a new bug in code), you
  wouldn't know unless you checked. Pairs with the Telegram bot —
  see `reference_telegram_bot.md` — but not wired yet.
- **No persistent jobs.** `JOB_REGISTRY` is in-memory. A restart loses
  in-flight dispatches. Fine for now (dispatches are short) but worth
  flagging.
- **Disk log rotation is local only.** No offsite archiving. Not worth
  it at current scale.
