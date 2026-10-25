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
        self._bg_tasks: set[asyncio.Task] = set()
        self._preload_lock = asyncio.Lock()
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

    def _bg(self, coro):
        """Run a side effect (webhook, hook) without ever delaying the alarm."""
        t = asyncio.create_task(coro)
        self._bg_tasks.add(t)
        t.add_done_callback(self._bg_tasks.discard)

    async def wait_background(self):
        """Wait for pending webhooks/hooks (tests and shutdown)."""
        if self._bg_tasks:
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)

    async def _emit(self, event: str, **data):
        url = self.cfg.events.webhook_url
        if not url:
            return
        try:
            self._http = self._http or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))
            await self._http.post(url, json={"source": "alarm-plus", "event": event, "at": datetime.now().isoformat(timespec="seconds"), **data})
        except Exception as e:
            log.warning("webhook failed: %s", e)

    def run_hook(self, name: str, alarm: dict, **extra):
        """Fire-and-forget: run hooks.<name> as a shell command on this device."""
        cmd = getattr(self.cfg.hooks, name).strip()
        if cmd:
            self._bg(self._hook(name, cmd, alarm, extra))

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
        async with self._preload_lock:  # startup preload and a first ring must not build two detectors
            if self.detector is None:
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
                try:
                    await self.ring(nxt[1])
                except Exception:  # one failed alarm must never kill the scheduler
                    log.exception("alarm %s failed", nxt[1].get("id"))
                    await asyncio.sleep(1)

    async def ring(self, alarm: dict):
        if self.ringing:
            return
        self.ringing = True  # first thing: a second request during the awaits below must see it
        self._dismiss.clear()
        s, r = self.cfg.smile, self.cfg.ring
        gate = SmileGate(s.threshold, s.hold_seconds)
        loop = asyncio.get_running_loop()
        t0 = last_frame = dbg_t = time.monotonic()
        last_ts, outcome, det_errors, cam_open = 0.0, "timeout", 0, False
        self._publish(state="ringing", alarm=alarm, score=None, progress=0.0, degraded=False)
        log.info("alarm ringing: id=%s label=%r time=%s", alarm.get("id"), alarm.get("label", ""), alarm.get("time"))
        self._bg(self._emit("ringing", alarm=alarm))
        self.run_hook("on_ring", alarm)
        try:
            if getattr(self.audio, "players", None):  # e.g. a sound test is still playing
                await self.audio.stop()
            await self.audio.start()  # sound first: loading the detector must never delay the alarm
            players = [type(p).__name__ for p in getattr(self.audio, "players", [])]
            log.info("audio started: %s", ", ".join(players) or "NO BACKEND STARTED (see audio warnings above)")
            if self.detector is None:
                try:
                    await self.preload()
                except Exception:
                    log.exception("smile detector could not be loaded: the alarm rings until it is stopped manually")
                    self._publish(degraded=True)
            self.cam.acquire()
            cam_open = True
            period = 1.0 / s.fps
            while True:
                tick = time.monotonic()
                if self._dismiss.is_set():
                    outcome = "manual"
                    break
                if tick - t0 > r.max_minutes * 60:
                    break
                frame, ts = self.cam.latest()
                if frame is not None and ts != last_ts and self.detector is not None:
                    last_ts = last_frame = ts
                    try:
                        score = await loop.run_in_executor(None, self.detector.score, frame)
                        det_errors = 0
                    except Exception:  # a bad frame must not end the alarm
                        det_errors += 1
                        score = None
                        if det_errors == 1:
                            log.exception("smile detector failed on a frame")
                        if det_errors == 5:
                            log.error("smile detector keeps failing: manual stop unlocked")
                            self._publish(degraded=True)
                    done = gate.update(score, time.monotonic())
                    if log.isEnabledFor(logging.DEBUG) and time.monotonic() - dbg_t > 1:
                        dbg_t = time.monotonic()
                        log.debug("ring: score=%s progress=%.2f camera=%s", score, gate.progress, self.cam.stats() if hasattr(self.cam, "stats") else "-")
                    self._publish(score=None if score is None else round(score, 2), progress=round(gate.progress, 2))
                    if done:
                        outcome = "smile"
                        break
                elif frame is not None and ts != last_ts:
                    last_ts = last_frame = ts  # camera works, detector doesn't (already degraded)
                elif tick - last_frame > r.camera_fail_seconds and not self.state.get("degraded"):
                    log.error("camera silent for %ss: manual dismiss unlocked", r.camera_fail_seconds)
                    self._publish(degraded=True)
                await asyncio.sleep(max(0.01, period - (time.monotonic() - tick)))
        finally:
            try:
                await self.audio.stop()
            finally:
                if cam_open:
                    self.cam.release()
                self.ringing = False
                self._publish(state="idle", alarm=None, score=None, progress=0.0, degraded=False)
        if alarm.get("once") and alarm["id"] in {a["id"] for a in self.store.alarms}:
            self.store.update(alarm["id"], {"enabled": False})
        secs = round(time.monotonic() - t0)
        self.last = {"id": alarm.get("id"), "label": alarm.get("label", ""), "reason": outcome, "seconds": secs,
                     "at": datetime.now().isoformat(timespec="seconds")}
        self._bg(self._emit("dismissed", alarm=alarm, reason=outcome, seconds=secs))
        self.run_hook("on_dismiss", alarm, reason=outcome, seconds=secs)
        log.info("alarm %s ended: %s after %ss", alarm.get("id"), outcome, secs)

    async def close(self):
        for t in list(self._bg_tasks):
            t.cancel()
        if self._http:
            await self._http.close()
