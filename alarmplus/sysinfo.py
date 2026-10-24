"""Host health for the web UI. Linux /proc based (no psutil); every probe returns None elsewhere."""
import asyncio
import logging
import os
import platform
import shutil
import socket
import time
from collections import deque
from pathlib import Path


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text()
    except OSError:
        return None


def parse_cpu_stat(text: str) -> tuple[int, int] | None:
    """(busy, total) jiffies from the first line of /proc/stat."""
    try:
        v = [int(x) for x in text.splitlines()[0].split()[1:9]]
    except (ValueError, IndexError):
        return None
    if len(v) < 5:
        return None
    idle = v[3] + v[4]  # idle + iowait
    return sum(v) - idle, sum(v)


def parse_meminfo(text: str) -> dict | None:
    kv = {}
    for line in text.splitlines():
        k, _, rest = line.partition(":")
        if rest.split():
            kv[k] = int(rest.split()[0]) * 1024
    if "MemTotal" not in kv or "MemAvailable" not in kv:
        return None
    used = kv["MemTotal"] - kv["MemAvailable"]
    return {"total_mb": round(kv["MemTotal"] / 2**20), "used_mb": round(used / 2**20), "percent": round(100 * used / kv["MemTotal"], 1)}


def decode_throttled(v: int) -> list[str]:
    bits = {0: "under-voltage now", 1: "frequency capped now", 2: "throttled now", 3: "soft temp limit now",
            16: "under-voltage since boot", 17: "frequency capped since boot", 18: "throttled since boot", 19: "soft temp limit since boot"}
    return [m for b, m in bits.items() if v >> b & 1]


class Sampler:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self._cpu = None  # last (busy, total)
        self._cpu_pct = None
        self._thr = (0.0, None)
        self._start = time.time()

    def cpu_percent(self) -> float | None:
        cur = parse_cpu_stat(_read("/proc/stat") or "")
        if cur and self._cpu and cur[1] > self._cpu[1]:
            self._cpu_pct = round(100 * (cur[0] - self._cpu[0]) / (cur[1] - self._cpu[1]), 1)
        self._cpu = cur or self._cpu
        return self._cpu_pct

    async def throttled(self):
        if time.monotonic() - self._thr[0] < 10 or not shutil.which("vcgencmd"):
            return self._thr[1]
        try:
            p = await asyncio.create_subprocess_exec("vcgencmd", "get_throttled", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out = (await asyncio.wait_for(p.communicate(), 2))[0].decode()
            self._thr = (time.monotonic(), decode_throttled(int(out.split("=")[1], 16)))
        except Exception:
            self._thr = (time.monotonic(), None)
        return self._thr[1]

    async def snapshot(self) -> dict:
        temp = _read("/sys/class/thermal/thermal_zone0/temp")
        rss = None
        for line in (_read("/proc/self/status") or "").splitlines():
            if line.startswith("VmRSS:"):
                rss = round(int(line.split()[1]) / 1024)
        up = _read("/proc/uptime")
        try:
            du = shutil.disk_usage(self.data_dir if os.path.isdir(self.data_dir) else ".")
            disk = {"free_mb": round(du.free / 2**20), "percent": round(100 * du.used / du.total, 1)}
        except OSError:
            disk = None
        return {
            "cpu_percent": self.cpu_percent(),
            "cpu_count": os.cpu_count(),
            "load": [round(x, 2) for x in os.getloadavg()] if hasattr(os, "getloadavg") else None,
            "mem": parse_meminfo(_read("/proc/meminfo") or ""),
            "cpu_temp_c": round(int(temp) / 1000, 1) if temp and temp.strip().isdigit() else None,
            "throttled": await self.throttled(),
            "process_rss_mb": rss,
            "disk": disk,
            "host_uptime_s": round(float(up.split()[0])) if up else None,
            "service_uptime_s": round(time.time() - self._start),
            "hostname": socket.gethostname(),
            "python": platform.python_version(),
            "platform": platform.platform(terse=True),
        }


async def tcp_ok(host: str, port: int, timeout=1.5):
    """(reachable, ms)"""
    t = time.monotonic()
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        w.close()
        return True, round((time.monotonic() - t) * 1000)
    except Exception:
        return False, None


class LogBuffer(logging.Handler):
    """Last N log lines, so the UI can show them without shell access."""

    def __init__(self, n=200):
        super().__init__()
        self.lines = deque(maxlen=n)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, record):
        self.lines.append(self.format(record))
