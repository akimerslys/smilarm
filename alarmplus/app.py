"""aiohttp app: REST + SSE + MJPEG preview + static UI."""
import asyncio
import hmac
import json
from pathlib import Path

import cv2
from aiohttp import web

from .audio import Audio
from .camera import Camera
from .engine import Engine
from .store import Store

WEB = Path(__file__).resolve().parent.parent / "web"


def build(cfg) -> web.Application:
    data = Path(cfg.data_dir)
    store = Store(data / "alarms.json")
    builtin = data / "alarm.wav"
    audio = Audio(cfg, builtin)
    cam = Camera(cfg.camera.source, cfg.camera.width)
    engine = Engine(cfg, store, audio, cam)

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
        app["tasks"] = [asyncio.create_task(engine.preload()), asyncio.create_task(engine.run())]

    async def on_stop(app):
        for t in app["tasks"]:
            t.cancel()
        await audio.stop()
        await engine.close()

    app = web.Application(middlewares=[auth])
    app.add_routes(r)
    app.on_startup.append(on_start)
    app.on_cleanup.append(on_stop)
    return app
