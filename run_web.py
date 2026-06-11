"""Launch the FastAPI web dashboard."""

from __future__ import annotations

import uvicorn

from config.settings import WEB_HOST, WEB_PORT


def main() -> None:
    uvicorn.run(
        "web.app:app",
        host=WEB_HOST,
        port=WEB_PORT,
        reload=False,
    )


if __name__ == "__main__":
    main()
