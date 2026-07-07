"""Entry point for ``python -m pdftransl`` — delegates to the CLI."""

from pdftransl.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
