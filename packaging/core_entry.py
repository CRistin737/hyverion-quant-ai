"""Frozen entry point for the Hyverion core sidecar used by the Tauri desktop app.

The shell launches ``hyverion-core api --port <n>`` with CONTROL_API_TOKEN and
HYVERION_SHELL_PID in the environment. Any other CLI command (``run``, ``doctor``,
...) works too, so the same binary can host the PAPER engine.
"""

from trading_bot.main import app

if __name__ == "__main__":
    app()
