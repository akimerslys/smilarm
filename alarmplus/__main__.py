import argparse
import logging
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser(prog="alarmplus")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--write-config", metavar="PATH", help="write a config file containing every default value, then exit")
    p.add_argument("--port", type=int, help="with --write-config: the web port to write instead of the default 8080")
    p.add_argument("--force", action="store_true", help="with --write-config: overwrite an existing file")
    p.add_argument("--check-config", metavar="PATH", help="validate a config file, then exit (status 1 if it has problems)")
    args = p.parse_args()

    if args.write_config:
        from .cfgtools import render_defaults

        path = Path(args.write_config)
        if path.exists() and not args.force:
            sys.exit(f"{path} already exists, not overwriting")
        path.write_text(render_defaults(args.port), encoding="utf-8", newline="\n")
        print(f"wrote {path}")
        return
    if args.check_config:
        from .cfgtools import check

        problems = check(args.check_config)
        for x in problems:
            print("  -", x)
        sys.exit(1 if problems else 0)

    import os

    from aiohttp import web

    from . import config
    from .app import build

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    cfg_path = Path(args.config).resolve()
    if cfg_path.exists():
        os.chdir(cfg_path.parent)  # relative paths in config.toml (data_dir, model) are relative to the file, wherever we were started from
    cfg = config.load(str(cfg_path))
    # access_log=None: the app logs only what matters (changes and errors), not every UI poll
    web.run_app(build(cfg, str(cfg_path)), host=cfg.server.host, port=cfg.server.port, print=None, access_log=None)


main()
