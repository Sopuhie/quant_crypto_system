"""Launch the stdlib HTTP web dashboard."""

from __future__ import annotations

from web.app import run_server


def main() -> None:
    run_server()


if __name__ == "__main__":
    main()
