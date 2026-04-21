"""
webapp/watchdog.py — watch the Company OS webapp and restart it if unhealthy.

Checks `/healthz/deep` every N seconds. Restarts the webapp if:
  - the endpoint returns non-200 for 3 consecutive checks, OR
  - the webapp has been up for > MAX_UPTIME_HOURS (proactive restart to
    sidestep slow state rot).

Usage:
    python webapp/watchdog.py
    python webapp/watchdog.py --interval 60 --max-uptime-hours 20
    python webapp/watchdog.py --url http://127.0.0.1:5050/healthz/deep

Running it:
  - Bare (foreground): `python webapp/watchdog.py` — logs to stdout
  - Background: leave it running in a persistent terminal, or
    register as a Windows Scheduled Task (see `docs/runbook-webapp.md`).

Why not just have the webapp restart itself?
  Because the failure mode we observed (22h rot on Flask dev server)
  kept /healthz returning 200 while company routes 500'd. A process
  cannot reliably detect its own corruption. Watchdog = separate
  process checking from outside.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:5050/healthz/deep"
DEFAULT_INTERVAL = 60                  # seconds between checks
DEFAULT_CONSECUTIVE_FAILS = 3          # fails before restart
DEFAULT_MAX_UPTIME_HOURS = 20          # proactive restart at 20h
DEFAULT_TIMEOUT = 8                    # healthcheck HTTP timeout


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{ts}] watchdog: {msg}", flush=True)


def check(url: str, timeout: float) -> tuple[bool, str]:
    """Hit the deep healthcheck. Return (ok, reason)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            status = resp.status
            if status == 200:
                return True, "ok"
            body = resp.read(500).decode("utf-8", errors="replace")
            return False, f"status={status}: {body[:200]}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def start_webapp(cmd: list[str], env: dict) -> subprocess.Popen:
    _log(f"starting: {' '.join(cmd)}")
    return subprocess.Popen(cmd, env=env)


def stop_webapp(proc: subprocess.Popen, timeout: float = 10.0) -> None:
    if proc is None or proc.poll() is not None:
        return
    _log(f"stopping pid {proc.pid}")
    try:
        # Windows: Popen.terminate() sends CTRL_BREAK-ish; gentler than kill.
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _log(f"pid {proc.pid} did not exit cleanly, killing")
            proc.kill()
            proc.wait(timeout=5)
    except Exception as exc:
        _log(f"stop failed: {exc}")


def run(
    *,
    url: str,
    interval: int,
    consecutive_fails: int,
    max_uptime_hours: float,
    timeout: float,
    launch_cmd: list[str],
) -> None:
    """Main loop. Starts the webapp, watches it, restarts on failure.
    Never returns unless interrupted."""
    env = os.environ.copy()
    proc: subprocess.Popen | None = None
    started_at: float = 0.0
    consecutive = 0

    def _start():
        nonlocal proc, started_at, consecutive
        proc = start_webapp(launch_cmd, env)
        started_at = time.time()
        consecutive = 0
        # Give it a grace period before first check.
        time.sleep(4)

    def _restart(reason: str):
        _log(f"restarting webapp: {reason}")
        stop_webapp(proc)
        _start()

    _start()

    def _sigterm_handler(signum, frame):
        _log("received shutdown signal")
        stop_webapp(proc)
        sys.exit(0)

    signal.signal(signal.SIGINT, _sigterm_handler)
    try:
        signal.signal(signal.SIGTERM, _sigterm_handler)
    except (AttributeError, ValueError):  # Windows has limited SIGTERM
        pass

    while True:
        time.sleep(interval)
        # Has the process died on its own?
        if proc is not None and proc.poll() is not None:
            _log(f"webapp process exited with code {proc.returncode}")
            _restart("process died")
            continue

        # Proactive age-out
        uptime_hours = (time.time() - started_at) / 3600.0
        if uptime_hours >= max_uptime_hours:
            _restart(f"uptime {uptime_hours:.1f}h >= {max_uptime_hours}h (proactive)")
            continue

        # Deep healthcheck
        ok, reason = check(url, timeout=timeout)
        if ok:
            if consecutive > 0:
                _log(f"recovered after {consecutive} fail(s)")
            consecutive = 0
            continue

        consecutive += 1
        _log(f"healthcheck FAIL ({consecutive}/{consecutive_fails}): {reason}")
        if consecutive >= consecutive_fails:
            _restart(f"{consecutive} consecutive healthcheck fails")


def main() -> None:
    parser = argparse.ArgumentParser(description="Watchdog for the Company OS webapp")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    parser.add_argument("--consecutive-fails", type=int, default=DEFAULT_CONSECUTIVE_FAILS)
    parser.add_argument("--max-uptime-hours", type=float, default=DEFAULT_MAX_UPTIME_HOURS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--no-prod", action="store_true",
        help="Launch the webapp in dev mode (Flask) instead of prod (Waitress). "
             "Only use for debugging — dev mode is what caused the original 22h rot.",
    )
    args = parser.parse_args()

    # Build the command that launches the webapp. Watchdog runs from
    # the company-os/ dir so this path is stable.
    webapp_path = Path(__file__).resolve().parent / "app.py"
    cmd = [sys.executable, str(webapp_path), "--host", args.host, "--port", str(args.port)]
    if not args.no_prod:
        cmd.append("--prod")

    _log(f"watchdog starting (check every {args.interval}s, max uptime {args.max_uptime_hours}h)")
    _log(f"launch command: {' '.join(cmd)}")
    run(
        url=args.url,
        interval=args.interval,
        consecutive_fails=args.consecutive_fails,
        max_uptime_hours=args.max_uptime_hours,
        timeout=args.timeout,
        launch_cmd=cmd,
    )


if __name__ == "__main__":
    main()
