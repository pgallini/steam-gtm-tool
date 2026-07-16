"""Local compatibility launcher; production uses ``gunicorn ui.app:app``."""

import os

from ui.app import app


def main() -> None:
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=False)


if __name__ == "__main__":
    main()
