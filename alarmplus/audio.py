"""Audio backends. Local playback is the core; moOde (Rpi3 + speakers) is an optional extra."""
import asyncio
import json
import logging
import math
import shutil
import socket
import struct
import wave
from pathlib import Path

log = logging.getLogger("audio")
MPV_SOCK = "/tmp/alarmplus-mpv.sock"


def make_builtin_tone(path: Path):
    """Generate a looping 'beep-beep-beep-beep ... pause' tone (no asset files needed)."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    sr, frames = 22050, bytearray()
    for _ in range(4):
        n_on, n_off = int(sr * 0.15), int(sr * 0.10)
        for i in range(n_on):
            env = min(1, i / 200, (n_on - i) / 200)
            frames += struct.pack("<h", int(26000 * env * math.sin(2 * math.pi * 1760 * i / sr)))
        frames += b"\x00\x00" * n_off
    frames += b"\x00\x00" * int(sr * 0.6)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(sr)
        w.writeframes(bytes(frames))


class LocalPlayer:
    def __init__(self, sound: str):
        self.sound, self.proc = sound, None

    async def start(self, volume: int):
        if mpv := shutil.which("mpv"):
            cmd = [mpv, "--no-video", "--really-quiet", "--loop=inf", f"--volume={volume}",
                   f"--input-ipc-server={MPV_SOCK}", self.sound]
        elif ff := shutil.which("ffplay"):
            cmd = [ff, "-nodisp", "-loglevel", "quiet", "-loop", "0", self.sound]
        elif ap := shutil.which("aplay"):
            cmd = ["sh", "-c", f'while :; do {ap} -q "{self.sound}"; done']
        else:
            raise RuntimeError("no audio player found (install mpv)")
        self.proc = await asyncio.create_subprocess_exec(*cmd)

    async def set_volume(self, v: int):
        try:
            _, w = await asyncio.open_unix_connection(MPV_SOCK)
            w.write(json.dumps({"command": ["set_property", "volume", v]}).encode() + b"\n")
            await w.drain()
            w.close()
        except OSError:
            pass  # non-mpv player: fixed system volume

    async def stop(self):
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 2)
            except asyncio.TimeoutError:
                self.proc.kill()
        self.proc = None


class MoodePlayer:
    """Minimal MPD client (moOde runs MPD on :6600)."""

    def __init__(self, host: str, port: int, url: str):
        self.host, self.port, self.url = host, port, url
        self._r = self._w = None

    async def _cmd(self, line: str):
        self._w.write(line.encode() + b"\n")
        await self._w.drain()
        while True:
            resp = (await asyncio.wait_for(self._r.readline(), 5)).decode().strip()
            if resp == "OK":
                return
            if resp.startswith("ACK") or not resp:
                raise RuntimeError(resp or "connection closed")

    async def start(self, volume: int):
        self._r, self._w = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), 4)
        await asyncio.wait_for(self._r.readline(), 4)  # "OK MPD x.y.z"
        url = self.url.replace('"', "%22")
        for c in ("stop", "clear", f'add "{url}"', "repeat 1", "single 0", f"setvol {volume}", "play"):
            try:
                await self._cmd(c)
            except RuntimeError as e:
                if c.startswith("setvol"):  # moOde fixed-volume mode
                    continue
                raise

    async def set_volume(self, v: int):
        try:
            await self._cmd(f"setvol {v}")
        except Exception:
            pass

    async def stop(self):
        if self._w:
            try:
                await self._cmd("stop")
            except Exception:
                pass
            self._w.close()
        self._w = None


def _own_ip_towards(host: str) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 1))
        return s.getsockname()[0]
    finally:
        s.close()


class Audio:
    def __init__(self, cfg, builtin: Path):
        self.cfg, self.builtin = cfg, builtin
        make_builtin_tone(builtin)
        self.players: list = []
        self._ramp: asyncio.Task | None = None

    async def _try(self, p, vol) -> bool:
        try:
            await p.start(vol)
            self.players.append(p)
            return True
        except Exception as e:
            log.warning("%s failed: %s", type(p).__name__, e)
            await p.stop()
            return False

    async def start(self):
        a, m = self.cfg.audio, self.cfg.moode
        v0 = a.volume_start
        want_moode = a.backend in ("moode", "both")
        ok_moode = False
        if want_moode:
            try:
                url = m.url or f"http://{_own_ip_towards(m.host)}:{self.cfg.server.port}/sound/alarm.wav"
            except OSError as e:
                log.warning("cannot resolve moOde host: %s", e)
            else:
                ok_moode = await self._try(MoodePlayer(m.host, m.port, url), v0)
        if a.backend in ("local", "both") or (want_moode and not ok_moode and m.fallback_local):
            await self._try(LocalPlayer(a.sound or str(self.builtin)), v0)
        if not self.players:
            log.error("no audio backend started")
        self._ramp = asyncio.create_task(self._ramp_volume())

    async def _ramp_volume(self):
        a = self.cfg.audio
        steps = max(1, int(a.ramp_seconds))
        for i in range(1, steps + 1):
            await asyncio.sleep(1)
            v = round(a.volume_start + (a.volume_max - a.volume_start) * i / steps)
            for p in self.players:
                await p.set_volume(v)

    async def stop(self):
        if self._ramp:
            self._ramp.cancel()
        for p in self.players:
            await p.stop()
        self.players = []
