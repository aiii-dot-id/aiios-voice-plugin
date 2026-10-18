"""Run the standalone application without starting or replacing model servers."""

import argparse
import json
from pathlib import Path

from aiohttp import web

from .server import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    web.run_app(
        create_app(config), host="127.0.0.1", port=config["port"], access_log=None
    )


if __name__ == "__main__":
    main()
