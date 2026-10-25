import argparse
import logging
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser(prog="alarmplus")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--write-config", metavar="PATH", help="write a config file containing every default value, then exit")
    p.add_argument("--check-config", metavar="PATH", help="validate a config file, then exit (status 1 if it has problems)")
    args = p.parse_args()

    if args.write_config:
        from .cfgtools import render_defaults

        path = Path(args.write_config)
        if path.exists():
            sys.exit(f"{path} already exists, not overwriting")
        path.write_text(render_defaults(), encoding="utf-8", newline="\n")
        print(f"wrote {path}")
        return
    if args.check_config:
        from .cfgtools import check

        problems = check(args.check_config)
        for x in problems:
            print("  -", x)
        sys.exit(1 if problems else 0)

    from aiohttp import web

    from . import config
    from .app import build

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    cfg = config.load(args.config)
    # access_log=None: the app logs only what matters (changes and errors), not every UI poll
    web.run_app(build(cfg, args.config), host=cfg.server.host, port=cfg.server.port, print=None, access_log=None)


main()
