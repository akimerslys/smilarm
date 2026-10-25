"""aiohttp app: REST + SSE + MJPEG preview + static UI."""
import asyncio
import hmac
import importlib.metadata as md
import json
import logging
import os
import signal
import time
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urlparse

import cv2
from aiohttp import web

from . import clock
from .audio import Audio
from .camera import Camera
from .engine import Engine
from .settings import Settings
from .smile import SmileGate
from .store import Store
from .sysinfo import LogBuffer, Sampler, in_docker, tcp_ok

WEB = Path(__file__).resolve().parent.parent / "web"
TASKS = web.AppKey("tasks", list)
ENGINE = web.AppKey("engine", Engine)
CAM = web.AppKey("cam", Camera)
AUDIO = web.AppKey("audio", Audio)
SETTINGS = web.AppKey("settings", Settings)
RESTART = web.AppKey("restart", object)


def annotate(img, score, thr, progress):
    """Draw the smile verdict on a frame (green = smiling, red = not, grey = no face)."""
    h, w = img.shape[:2]
    if score is None:
        col, txt = (160, 160, 160), "NO FACE"
    elif score >= thr:
        col, txt = (60, 200, 60), f"SMILING {score:.2f}"
    else:
        col, txt = (60, 60, 230), f"not smiling {score:.2f}"
    cv2.rectangle(img, (0, 0), (w - 1, h - 1), col, 4)
    cv2.putText(img, txt, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4)
    cv2.putText(img, txt, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, col, 2)
    cv2.rectangle(img, (0, h - 10), (int(w * progress), h), col, -1)
    return img


def build(cfg, config_path: str | None = None) -> web.Application:
    data = Path(cfg.data_dir)
    settings = Settings(cfg, Path(config_path) if config_path else data / "config.toml", data / "settings.json")
    logbuf, sampler, net = LogBuffer(), Sampler(cfg.data_dir), {"at": 0.0, "v": {}}
    logging.getLogger().addHandler(logbuf)
    apply_level = lambda: logging.getLogger().setLevel(getattr(logging, cfg.logging.level, logging.INFO))  # noqa: E731
    apply_level()
    settings.listeners.append(apply_level)
    det_key = [(cfg.smile.backend, cfg.smile.model)]

    def reset_detector():
        key = (cfg.smile.backend, cfg.smile.model)
        if key != det_key[0] and not engine_ref[0].ringing:  # picked up by the next ring / smile test
            det_key[0], engine_ref[0].detector = key, None
            log.info("smile detector settings changed: will reload (%s)", key[0])
    engine_ref = [None]
    settings.listeners.append(reset_detector)
    log = logging.getLogger("app")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)  # per-request lines are replaced by the access_log middleware
    store = Store(data / "alarms.json")
    builtin = data / "alarm.wav"
    audio = Audio(cfg, builtin)
    cam = Camera(cfg.camera.source, cfg.camera.quality, cfg.camera.width, cfg.camera.rotate, cfg.camera.flashlight, cfg.camera.flashlight_level)
    settings.listeners.append(lambda: cam.reconfigure(cfg.camera.source, cfg.camera.quality, cfg.camera.width, cfg.camera.rotate, cfg.camera.flashlight, cfg.camera.flashlight_level))
    engine = Engine(cfg, store, audio, cam)
    engine_ref[0] = engine
    probe = {"active": False, "stats": {}, "sound": False}

    @web.middleware
    async def auth(request, handler):
        tok = cfg.server.token
        if tok and request.path.startswith("/api/"):
            given = request.headers.get("Authorization", "").removeprefix("Bearer ") or request.query.get("token", "")
            if not hmac.compare_digest(given.encode(), tok.encode()):  # bytes: str compare raises on non-ASCII
                raise web.HTTPUnauthorized(text="token required")
        return await handler(request)

    @web.middleware
    async def access_log(request, handler):
        """Log what matters instead of every UI poll: changes (non-GET) and failures."""
        try:
            resp = await handler(request)
        except web.HTTPException as e:
            if e.status >= 400 and e.status != 401:
                log.warning("%s %s -> %s %s", request.method, request.path, e.status, (e.text or "")[:120])
            raise
        if request.method != "GET":
            log.info("%s %s -> %s (from %s)", request.method, request.path, resp.status, request.remote)
        else:
            log.debug("GET %s -> %s", request.path, resp.status)  # path only: ?token= must not reach the logs
        return resp

    async def body(req):
        try:
            data = await req.json()
        except Exception:
            raise web.HTTPBadRequest(text="invalid JSON")
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text="expected a JSON object")
        return data

    def guard(fn, *a):
        try:
            return fn(*a)
        except ValueError as e:
            raise web.HTTPBadRequest(text=str(e))

    r = web.RouteTableDef()

    @r.get("/")
    async def index(_):
        return web.FileResponse(WEB / "index.html")

    @r.get("/sound/alarm.wav")
    async def sound(_):
        return web.FileResponse(builtin)

    @r.get("/sound/current")
    async def sound_current(_):
        p = Path(audio.current)
        if not p.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(p)

    def sp(**extra):
        return web.json_response({"settings": settings.describe(), "locked": settings.locked,
                                  "restart_needed": settings.restart_needed, **extra})

    @r.get("/api/settings")
    async def get_settings(_):
        return sp()

    @r.put("/api/settings")
    async def put_settings(req):
        try:
            settings.update(await body(req))
        except PermissionError as e:
            raise web.HTTPForbidden(text=str(e))
        except ValueError as e:
            raise web.HTTPBadRequest(text=json.dumps(e.args[0]) if isinstance(e.args[0], dict) else str(e))
        except OSError as e:
            raise web.HTTPInternalServerError(text=f"could not write {settings.path}: {e}")
        return sp(file=str(settings.path))

    @r.post("/api/settings/reload")
    async def reload_settings(_):
        """Re-read config.toml after it was edited by hand."""
        problems = settings.reload()
        return sp(problems=problems)

    @r.delete("/api/settings")
    async def restore_defaults(_):
        try:
            settings.restore_defaults()
        except OSError as e:
            raise web.HTTPInternalServerError(text=f"could not write {settings.path}: {e}")
        return sp()

    @r.get("/api/logs")
    async def logs(req):
        n = max(1, min(200, int(req.query.get("n", 100) or 100)))
        return web.json_response({"lines": list(logbuf.lines)[-n:]})

    @r.get("/api/system")
    async def system(_):
        now = time.monotonic()
        if now - net["at"] > 10:  # reachability checks at most every 10 s
            net["at"] = now
            cs = urlparse(str(cam.source))
            checks = {}
            if cs.hostname:
                checks["camera"] = await tcp_ok(cs.hostname, 80)
            if cfg.audio.backend in ("moode", "both"):
                checks["moode"] = await tcp_ok(cfg.moode.host, cfg.moode.port)
            net["v"] = {k: {"reachable": ok, "ms": ms} for k, (ok, ms) in checks.items()}
        vers = {}
        for pkg in ("aiohttp", "mediapipe", "opencv-contrib-python", "opencv-python-headless", "opencv-python"):
            try:
                vers[pkg] = md.version(pkg)
            except md.PackageNotFoundError:
                pass
        return web.json_response({
            **await sampler.snapshot(), "in_docker": in_docker(), "device_time": clock.now(cfg).isoformat(timespec="seconds"),
            "timezone": cfg.general.timezone or "system default", "network": net["v"], "versions": vers, "last_alarm": engine.last,
            "hooks": engine.hook_log, "audio": {"current": audio.current, "players": [type(p).__name__ for p in audio.players]},
        })

    @r.get("/api/audio-devices")
    async def audio_devices(_):
        """Output devices the player can use here; the Settings page offers them for audio.device."""
        from .audio import list_devices

        return web.json_response({"devices": await list_devices()})

    @r.post("/api/restart")
    async def restart(_):
        """Needed after changing server.host / server.port. The supervisor (Docker, systemd) starts the process again."""
        if engine.ringing:
            raise web.HTTPConflict(text="an alarm is ringing: restart after it ends")
        log.warning("restart requested from the web UI")
        asyncio.get_running_loop().call_later(0.5, app[RESTART])
        return web.json_response({"ok": True})

    @r.get("/api/setup")
    async def setup(_):
        """A short checklist for the UI's first-run card: what still needs the user's attention."""
        cs, checks = urlparse(str(cam.source)), []
        local_utc = datetime.now().astimezone().utcoffset() == timedelta(0)
        if not cfg.general.timezone and (in_docker() or local_utc):
            checks.append({"id": "timezone", "level": "warn", "key": "general.timezone", "title": "Set your timezone",
                           "detail": "The device clock is on UTC, so a 07:00 alarm would ring at 07:00 UTC."})
        else:
            checks.append({"id": "timezone", "level": "ok", "title": "Timezone", "detail": cfg.general.timezone or "system default"})
        if cs.hostname == "alarmcam.local":
            checks.append({"id": "camera", "level": "todo", "key": "camera.source", "title": "Set the camera address",
                           "detail": "Still the placeholder. Enter http://<camera-ip>:81/stream (or 0 for a USB camera)."})
        elif cam.state == "error":
            checks.append({"id": "camera", "level": "warn", "key": "camera.source", "title": "Camera problem", "detail": cam.error})
        else:
            checks.append({"id": "camera", "level": "ok", "title": "Camera", "detail": str(cam.source)})
        backend = type(engine.detector).__name__ if engine.detector else None
        if backend == "HaarDetector":
            checks.append({"id": "detector", "level": "warn", "key": "smile.backend", "title": "Simple smile detector in use",
                           "detail": "MediaPipe could not be loaded (see the log). Haar works but is less accurate."})
        else:
            checks.append({"id": "detector", "level": "ok", "title": "Smile detector", "detail": backend or "loading..."})
        checks.append({"id": "audio", "level": "ok", "title": "Sound", "detail": f"{cfg.audio.backend}; press Test sound in System & debug to hear it"})
        checks.append({"id": "token", "level": "ok" if cfg.server.token else "info", "key": "server.token", "title": "Access token",
                       "detail": "set" if cfg.server.token else "Not set: anyone on your network can use this page. Recommended."})
        needs = [c for c in checks if c["level"] in ("todo", "warn")]
        return web.json_response({"checks": checks, "attention": len(needs), "in_docker": in_docker(), "restart_needed": settings.restart_needed})

    @r.post("/api/sound-test")
    async def sound_test(_):
        if engine.ringing or probe["sound"]:
            raise web.HTTPConflict(text="alarm ringing or sound test already running")
        probe["sound"] = True
        log.info("sound test: backend=%s", cfg.audio.backend)

        async def play():
            try:
                if engine.ringing:  # an alarm started in the meantime owns the speakers
                    return
                await audio.start()
                log.info("sound test playing: %s via %s", audio.current, [type(p).__name__ for p in audio.players] or "NO BACKEND STARTED")
                await asyncio.sleep(6)
            finally:
                if not engine.ringing:
                    await audio.stop()
                probe["sound"] = False

        asyncio.create_task(play())
        return web.json_response({"ok": True, "seconds": 6})

    @r.get("/api/state")
    async def state(_):
        return web.json_response({**engine.snapshot(), "alarms": store.alarms})

    def debug_info():
        s = cfg.smile
        st = dict(probe["stats"])
        now = time.monotonic()
        det_age = now - st.pop("updated", now) if probe["active"] else None
        cs = cam.stats()
        cam_stale = cs["cam_frame_age_ms"] is None or cs["cam_frame_age_ms"] > 3000
        return {
            "threshold": s.threshold, "hold_seconds": s.hold_seconds, "fps": s.fps,
            "backend": type(engine.detector).__name__ if engine.detector else None,
            "camera_source": str(cam.source), "quality": cam.quality, "rotate": cam.rotate, "detect_width": cam.width,
            "ringing": engine.ringing, "test_active": probe["active"],
            # tells the two stalls apart: camera not delivering vs. detector not answering
            "no_frames": bool(probe["active"] and cam_stale and (det_age or 0) > 3),
            "detector_stalled": bool(probe["active"] and not cam_stale and det_age is not None and det_age > 3),
            **cs, **st,
        }

    @r.get("/api/debug")
    async def debug(_):
        return web.json_response(debug_info())

    @r.get("/api/smile-test.mjpg")
    async def smile_test(req):
        """Live annotated camera view. Streaming runs at camera speed; detection runs beside it on
        the newest frame, so a slow detector never makes the picture lag. Numbers: /api/debug."""
        if engine.ringing or probe["active"]:
            raise web.HTTPConflict(text="alarm ringing or smile test already running")
        probe["active"] = True
        probe["stats"] = {"updated": time.monotonic()}
        log.info("smile test started (threshold %s, hold %ss)", cfg.smile.threshold, cfg.smile.hold_seconds)
        try:
            if engine.detector is None:
                try:
                    await engine.preload()
                except Exception as e:
                    log.exception("smile detector could not be loaded")
                    raise web.HTTPServiceUnavailable(text=f"smile detector could not be loaded: {e}")
            resp = web.StreamResponse(headers={"Content-Type": "multipart/x-mixed-replace; boundary=f", "Cache-Control": "no-cache"})
            await resp.prepare(req)
            sm = cfg.smile
            gate, loop = SmileGate(sm.threshold, sm.hold_seconds), asyncio.get_running_loop()
            verdict = {"score": None, "progress": 0.0}
            dbg = [0.0]

            async def detect():
                last, fps = 0.0, 0.0
                while True:
                    f, ts = cam.latest()
                    if f is None or ts == last:
                        await asyncio.sleep(0.02)
                        continue
                    last, t0 = ts, time.monotonic()
                    score = await loop.run_in_executor(None, engine.detector.score, f)
                    now = time.monotonic()
                    gate.update(score, now)
                    prev = probe["stats"].get("updated", now - 1)
                    fps = 0.7 * fps + 0.3 / max(now - prev, 1e-3)
                    verdict.update(score=score, progress=gate.progress)
                    if log.isEnabledFor(logging.DEBUG) and now - dbg[0] > 1:
                        dbg[0] = now
                        log.debug("smile test: score=%s progress=%.2f infer=%.0f ms camera=%s", score, gate.progress, (now - t0) * 1000, cam.stats())
                    probe["stats"] = {
                        "updated": now, "face": score is not None,
                        "score": None if score is None else round(score, 3),
                        "smiling": score is not None and score >= sm.threshold,
                        "progress": round(gate.progress, 2), "would_dismiss": gate.progress >= 1.0,
                        "infer_ms": round((now - t0) * 1000, 1), "detect_fps": round(fps, 1),
                        "detect_frame_age_ms": round((t0 - ts) * 1000), "frame_size": f"{f.shape[1]}x{f.shape[0]}",
                    }
                    await asyncio.sleep(max(0.0, 1 / sm.fps - (time.monotonic() - t0)))

            cam.acquire()
            task = asyncio.create_task(detect())
            try:
                last = 0.0
                while True:
                    f, ts = cam.latest()
                    if f is None or ts == last:
                        await asyncio.sleep(0.01)
                        continue
                    last, t0 = ts, time.monotonic()
                    img = annotate(f.copy(), verdict["score"], sm.threshold, verdict["progress"])
                    _, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    await resp.write(b"--f\r\nContent-Type: image/jpeg\r\n\r\n" + jpg.tobytes() + b"\r\n")
                    await asyncio.sleep(max(0.0, 1 / 15 - (time.monotonic() - t0)))  # cap the view at 15 fps
            except (ConnectionResetError, asyncio.CancelledError):
                pass
            finally:
                task.cancel()
                cam.release()
            return resp
        finally:
            probe["active"] = False
            log.info("smile test ended")

    @r.post("/api/alarms")
    async def add(req):
        a = guard(store.add, await body(req))
        engine.changed()
        return web.json_response(a, status=201)

    @r.put("/api/alarms/{id}")
    async def upd(req):
        a = guard(store.update, req.match_info["id"], await body(req))
        if not a:
            raise web.HTTPNotFound()
        engine.changed()
        return web.json_response(a)

    @r.delete("/api/alarms/{id}")
    async def rm(req):
        if not store.delete(req.match_info["id"]):
            raise web.HTTPNotFound()
        engine.changed()
        return web.json_response({"ok": True})

    @r.post("/api/test")
    async def test(_):
        if engine.ringing:
            raise web.HTTPConflict(text="already ringing")
        asyncio.create_task(engine.ring({"id": "test", "time": "now", "label": "test"}))
        return web.json_response({"ok": True})

    @r.post("/api/dismiss")
    async def dismiss(_):
        if not engine.dismiss():
            raise web.HTTPForbidden(text="Manual stop is disabled: smile at the camera, or enable ring.allow_manual_dismiss in Settings.")
        return web.json_response({"ok": True})

    @r.get("/api/events")
    async def events(req):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await resp.prepare(req)
        q = engine.subscribe()
        sse = lambda d: b"data: " + json.dumps(d).encode() + b"\n\n"
        try:
            await resp.write(sse(engine.snapshot()))
            while True:
                try:
                    await resp.write(sse(await asyncio.wait_for(q.get(), 15)))
                except asyncio.TimeoutError:
                    await resp.write(b": ping\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            engine.unsubscribe(q)
        return resp

    @r.get("/api/preview.mjpg")
    async def preview(req):
        resp = web.StreamResponse(headers={"Content-Type": "multipart/x-mixed-replace; boundary=f", "Cache-Control": "no-cache"})
        await resp.prepare(req)
        cam.acquire()
        last = 0.0
        try:
            while True:
                f, ts = cam.latest()
                if f is not None and ts != last:
                    last = ts
                    _, jpg = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    await resp.write(b"--f\r\nContent-Type: image/jpeg\r\n\r\n" + jpg.tobytes() + b"\r\n")
                await asyncio.sleep(0.15)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            cam.release()
        return resp

    async def preload_quietly():
        try:
            await engine.preload()
        except Exception:
            log.exception("smile detector could not be loaded (alarms will ring until stopped manually)")

    async def on_start(app):
        log.info("Alarm+ started: web :%s | camera %s (%s, rotate %s) | audio %s | config %s",
                 cfg.server.port, cam.source, cam.quality, cam.rotate, cfg.audio.backend, settings.path)
        app[TASKS] = [asyncio.create_task(preload_quietly()), asyncio.create_task(engine.run())]

    async def on_stop(app):
        for t in app[TASKS]:
            t.cancel()
        await audio.stop()
        await engine.close()
        logging.getLogger().removeHandler(logbuf)

    app = web.Application(middlewares=[access_log, auth])
    app[ENGINE], app[CAM], app[AUDIO], app[SETTINGS] = engine, cam, audio, settings
    app[RESTART] = lambda: os.kill(os.getpid(), signal.SIGTERM)  # replaceable in tests
    app.add_routes(r)
    app.on_startup.append(on_start)
    app.on_cleanup.append(on_stop)
    return app
