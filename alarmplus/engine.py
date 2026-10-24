"""Scheduler + ring loop: sleeps until the next alarm, rings until the user smiles."""
import asyncio
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import aiohttp

from .smile import SmileGate, make_detector

log = logging.getLogger("engine")


class Engine:
    def __init__(self, cfg, store, audio, camera):
        self.cfg, self.store, self.audio, self.cam = cfg, store, audio, camera
        self.detector = None
        self.ringing = False
        self.state = {"state": "idle"}
        self._wake = asyncio.Event()
        self._dismiss = asyncio.Event()
        self._subs: set[asyncio.Queue] = set()
        self._http: aiohttp.ClientSession | None = None
        self._hooks: set[asyncio.Task] = set()
        self.last: dict | None = None  # outcome of the most recent alarm
        self.hook_log: list[dict] = []

    # ---- pub/sub (SSE) ----
    def subscribe(self):
        q = asyncio.Queue(maxsize=16)
        self._subs.add(q)
        return q

    def unsubscribe(self, q):
        self._subs.discard(q)

    def _can_stop(self) -> bool:
        """Manual stop (web UI button) is allowed if enabled in settings, if the camera is dead,
        or for a *test* ring, which is never supposed to trap you."""
        return self.ringing and bool(
            self.cfg.ring.allow_manual_dismiss
            or self.state.get("degraded")
            or (self.state.get("alarm") or {}).get("id") == "test"
        )

    def _publish(self, **kw):
        self.state = {**self.state, **kw}
        self.state["can_stop"] = self._can_stop()
        for q in self._subs:
            if q.full():
                q.get_nowait()
            q.put_nowait(self.state)

    async def _emit(self, event: str, **data):
        url = self.cfg.events.webhook_url
        if not url:
            return
        try:
            self._http = self._http or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))
            await self._http.post(url, json={"source": "alarm-plus", "event": event, **data})
        except Exception as e:
            log.warning("webhook failed: %s", e)

    def run_hook(self, name: str, alarm: dict, **extra):
        """Fire-and-forget: run hooks.<name> as a shell command on this device."""
        cmd = getattr(self.cfg.hooks, name).strip()
        if cmd:
            t = asyncio.create_task(self._hook(name, cmd, alarm, extra))
            self._hooks.add(t)
            t.add_done_callback(self._hooks.discard)

    async def _hook(self, name, cmd, alarm, extra):
        env = {**os.environ, "ALARM_EVENT": name.removeprefix("on_"), "ALARM_ID": str(alarm.get("id", "")),
               "ALARM_LABEL": str(alarm.get("label", "")), "ALARM_TIME": str(alarm.get("time", "")),
               **{"ALARM_" + k.upper(): str(v) for k, v in extra.items()}}
        rec = {"hook": name, "cmd": cmd, "at": datetime.now().isoformat(timespec="seconds")}
        try:
            p = await asyncio.create_subprocess_shell(
                cmd, env=env, cwd=str(Path.home()), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            try:
                out, _ = await asyncio.wait_for(p.communicate(), self.cfg.hooks.timeout_seconds)
                rec.update(code=p.returncode, output=out.decode(errors="replace")[-500:])
            except asyncio.TimeoutError:
                p.kill()
                rec.update(code=None, output=f"killed after {self.cfg.hooks.timeout_seconds}s")
        except Exception as e:
            rec.update(code=None, output=str(e))
        log.log(logging.INFO if rec["code"] == 0 else logging.WARNING, "hook %s -> %s %s", name, rec["code"], rec["output"].strip()[:200])
        self.hook_log = (self.hook_log + [rec])[-10:]

    def snapshot(self):
        nxt = self.store.next(datetime.now())
        return {**self.state, "next": {"at": nxt[0].isoformat(timespec="minutes"), "id": nxt[1]["id"]} if nxt else None}

    def changed(self):
        self._wake.set()

    # ---- control ----
    def dismiss(self) -> bool:
        if not self._can_stop():
            return False
        self._dismiss.set()
        return True

    async def preload(self):
        s = self.cfg.smile
        self.detector = await asyncio.get_running_loop().run_in_executor(None, make_detector, s.backend, s.model)

    async def run(self):
        while True:
            nxt = self.store.next(datetime.now())
            wait = 60.0 if not nxt else min(60.0, max(0.0, (nxt[0] - datetime.now()).total_seconds()))
            try:
                await asyncio.wait_for(self._wake.wait(), wait)
                self._wake.clear()
                continue
            except asyncio.TimeoutError:
                pass
            if nxt and datetime.now() >= nxt[0]:
                await self.ring(nxt[1])

    async def ring(self, alarm: dict):
        if self.ringing:
            return
        if self.detector is None:
            await self.preload()
        self.ringing = True
        self._dismiss.clear()
        s, r = self.cfg.smile, self.cfg.ring
        gate = SmileGate(s.threshold, s.hold_seconds)
        loop = asyncio.get_running_loop()
        t0 = last_frame = time.monotonic()
        last_ts, outcome = 0.0, "timeout"
        self._publish(state="ringing", alarm=alarm, score=None, progress=0.0, degraded=False)
        await self._emit("ringing", alarm=alarm)
        self.run_hook("on_ring", alarm)
        self.cam.acquire()
        try:
            await self.audio.start()
            period = 1.0 / s.fps
            while True:
                tick = time.monotonic()
                if self._dismiss.is_set():
                    outcome = "manual"
                    break
                if tick - t0 > r.max_minutes * 60:
                    break
                frame, ts = self.cam.latest()
                if frame is not None and ts != last_ts:
                    last_ts = last_frame = ts
                    score = await loop.run_in_executor(None, self.detector.score, frame)
                    done = gate.update(score, time.monotonic())
                    self._publish(score=None if score is None else round(score, 2), progress=round(gate.progress, 2))
                    if done:
                        outcome = "smile"
                        break
                elif tick - last_frame > r.camera_fail_seconds and not self.state["degraded"]:
                    log.error("camera silent for %ss: manual dismiss unlocked", r.camera_fail_seconds)
                    self._publish(degraded=True)
                await asyncio.sleep(max(0.01, period - (time.monotonic() - tick)))
        finally:
            await self.audio.stop()
            self.cam.release()
            self.ringing = False
        if alarm.get("once") and alarm["id"] in {a["id"] for a in self.store.alarms}:
            self.store.update(alarm["id"], {"enabled": False})
        self._publish(state="idle", alarm=None, score=None, progress=0.0, degraded=False)
        secs = round(time.monotonic() - t0)
        self.last = {"id": alarm.get("id"), "label": alarm.get("label", ""), "reason": outcome, "seconds": secs,
                     "at": datetime.now().isoformat(timespec="seconds")}
        await self._emit("dismissed", alarm=alarm, reason=outcome, seconds=secs)
        self.run_hook("on_dismiss", alarm, reason=outcome, seconds=secs)
        log.info("alarm %s ended: %s", alarm.get("id"), outcome)

    async def close(self):
        for t in list(self._hooks):
            t.cancel()
        if self._http:
            await self._http.close()
