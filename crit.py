"""Crit Launcher entry point (the file PyInstaller builds)."""
from crit_launcher.app.main import main

if __name__ == "__main__":
    raise SystemExit(main() or 0)
