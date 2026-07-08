"""Точка входа ``python -m pdftransl`` — делегирует в CLI.
"""

from pdftransl.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
