"""aiohttp app: REST + SSE + MJPEG preview + static UI."""
import asyncio
import hmac
import json
import time
from pathlib import Path

import cv2
from aiohttp import web

from .audio import Audio
from .camera import Camera
from .engine import Engine
from .smile import SmileGate
from .store import Store

WEB = Path(__file__).resolve().parent.parent / "web"
TASKS = web.AppKey("tasks", list)
ENGINE = web.AppKey("engine", Engine)
CAM = web.AppKey("cam", Camera)


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


def build(cfg) -> web.Application:
    data = Path(cfg.data_dir)
    store = Store(data / "alarms.json")
    builtin = data / "alarm.wav"
    audio = Audio(cfg, builtin)
    cam = Camera(cfg.camera.source, cfg.camera.quality, cfg.camera.width)
    engine = Engine(cfg, store, audio, cam)
    probe = {"active": False, "stats": {}}

    @web.middleware
    async def auth(request, handler):
        tok = cfg.server.token
        if tok and request.path.startswith("/api/"):
            given = request.headers.get("Authorization", "").removeprefix("Bearer ") or request.query.get("token", "")
            if not hmac.compare_digest(given, tok):
                raise web.HTTPUnauthorized(text="token required")
        return await handler(request)

    async def body(req):
        try:
            return await req.json()
        except Exception:
            raise web.HTTPBadRequest(text="invalid JSON")

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

    @r.get("/api/state")
    async def state(_):
        return web.json_response({**engine.snapshot(), "alarms": store.alarms})

    def debug_info():
        s = cfg.smile
        st = dict(probe["stats"])
        stale = time.monotonic() - st.pop("updated", 0) if probe["active"] else None
        return {
            "threshold": s.threshold, "hold_seconds": s.hold_seconds, "fps": s.fps,
            "backend": type(engine.detector).__name__ if engine.detector else None,
            "camera_source": str(cam.source), "quality": cam.quality, "detect_width": cam.width,
            "ringing": engine.ringing, "test_active": probe["active"],
            "no_frames": bool(probe["active"] and stale is not None and stale > 3),
            **st,
        }

    @r.get("/api/debug")
    async def debug(_):
        return web.json_response(debug_info())

    @r.get("/api/smile-test.mjpg")
    async def smile_test(req):
        """Live annotated camera view + smile score; numbers are polled from /api/debug."""
        if engine.ringing or probe["active"]:
            raise web.HTTPConflict(text="alarm ringing or smile test already running")
        probe["active"] = True
        probe["stats"] = {}
        try:
            if engine.detector is None:
                await engine.preload()
            resp = web.StreamResponse(headers={"Content-Type": "multipart/x-mixed-replace; boundary=f", "Cache-Control": "no-cache"})
            await resp.prepare(req)
            sm = cfg.smile
            gate, loop = SmileGate(sm.threshold, sm.hold_seconds), asyncio.get_running_loop()
            cam.acquire()
            last, fps = 0.0, 0.0
            try:
                while True:
                    f, ts = cam.latest()
                    if f is None or ts == last:
                        await asyncio.sleep(0.03)
                        continue
                    last = ts
                    t0 = time.monotonic()
                    score = await loop.run_in_executor(None, engine.detector.score, f)
                    now = time.monotonic()
                    infer = now - t0
                    gate.update(score, now)
                    fps = 1 / max(now - probe["stats"].get("updated", now - 1), 1e-3) * 0.3 + fps * 0.7
                    probe["stats"] = {
                        "updated": now, "face": score is not None,
                        "score": None if score is None else round(score, 3),
                        "smiling": score is not None and score >= sm.threshold,
                        "progress": round(gate.progress, 2), "would_dismiss": gate.progress >= 1.0,
                        "infer_ms": round(infer * 1000, 1), "detect_fps": round(fps, 1),
                        "frame_age_ms": round((now - ts) * 1000), "frame_size": f"{f.shape[1]}x{f.shape[0]}",
                    }
                    _, jpg = cv2.imencode(".jpg", annotate(f.copy(), score, sm.threshold, gate.progress), [cv2.IMWRITE_JPEG_QUALITY, 70])
                    await resp.write(b"--f\r\nContent-Type: image/jpeg\r\n\r\n" + jpg.tobytes() + b"\r\n")
                    await asyncio.sleep(max(0.0, 1 / sm.fps - (time.monotonic() - t0)))
            except (ConnectionResetError, asyncio.CancelledError):
                pass
            finally:
                cam.release()
            return resp
        finally:
            probe["active"] = False

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
            raise web.HTTPForbidden(text="manual dismiss disabled: smile!")
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

    async def on_start(app):
        app[TASKS] = [asyncio.create_task(engine.preload()), asyncio.create_task(engine.run())]

    async def on_stop(app):
        for t in app[TASKS]:
            t.cancel()
        await audio.stop()
        await engine.close()

    app = web.Application(middlewares=[auth])
    app[ENGINE], app[CAM] = engine, cam
    app.add_routes(r)
    app.on_startup.append(on_start)
    app.on_cleanup.append(on_stop)
    return app
