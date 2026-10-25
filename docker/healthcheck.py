"""Docker HEALTHCHECK: is the web server answering? (401 counts: it is up, a token is just required.)"""
import os
import sys
import tomllib
import urllib.error
import urllib.request

host, port = "127.0.0.1", 8080
try:
    with open(os.path.join(os.environ.get("STATE_DIR", "/state"), "config.toml"), "rb") as f:
        server = tomllib.load(f).get("server", {})
    port = int(server.get("port", port))
    if server.get("host") not in (None, "", "0.0.0.0", "::"):
        host = server["host"]
except Exception:
    pass  # no/invalid config yet: check the default port

try:
    urllib.request.urlopen(f"http://{host}:{port}/api/state", timeout=4).read()
except urllib.error.HTTPError as e:
    sys.exit(0 if e.code == 401 else 1)
except Exception:
    sys.exit(1)
