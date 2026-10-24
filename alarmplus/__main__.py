import argparse
import logging

from aiohttp import web

from . import config
from .app import build

p = argparse.ArgumentParser(prog="alarmplus")
p.add_argument("--config", default="config.toml")
args = p.parse_args()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
cfg = config.load(args.config)
web.run_app(build(cfg), host=cfg.server.host, port=cfg.server.port, print=None)
