"""`python -m crit_launcher`"""
from .app.main import main

if __name__ == "__main__":
    raise SystemExit(main() or 0)
