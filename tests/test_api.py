import asyncio
import json

from aiohttp.test_utils import TestClient, TestServer

from alarmplus import config
from alarmplus.app import build


def run(coro_fn, tmp_path, token=""):
    cfg = config.load(None)
    cfg._d["data_dir"] = str(tmp_path)
    cfg._d["server"]["token"] = token

    async def main():
        async with TestClient(TestServer(build(cfg))) as cl:
            return await coro_fn(cl)

    return asyncio.run(main())


def test_crud_and_validation(tmp_path):
    async def go(cl):
        r = await cl.post("/api/alarms", json={"time": "06:45", "days": [0, 2], "label": "gym"})
        assert r.status == 201
        a = await r.json()
        assert (await (await cl.get("/api/state")).json())["alarms"][0]["time"] == "06:45"

        r = await cl.put(f"/api/alarms/{a['id']}", json={"enabled": False})
        assert (await r.json())["enabled"] is False
        assert (await (await cl.get("/api/state")).json())["next"] is None  # nothing enabled

        assert (await cl.post("/api/alarms", json={"time": "25:00"})).status == 400
        assert (await cl.post("/api/alarms", data="not json")).status == 400
        assert (await cl.put("/api/alarms/nope", json={"time": "07:00"})).status == 404
        assert (await cl.delete("/api/alarms/nope")).status == 404
        assert (await cl.delete(f"/api/alarms/{a['id']}")).status == 200
        assert (await (await cl.get("/api/state")).json())["alarms"] == []

    run(go, tmp_path)


def test_alarms_persist_across_restart(tmp_path):
    async def add(cl):
        await cl.post("/api/alarms", json={"time": "08:00"})

    async def read(cl):
        return (await (await cl.get("/api/state")).json())["alarms"]

    run(add, tmp_path)
    assert run(read, tmp_path)[0]["time"] == "08:00"


def test_token_auth(tmp_path):
    async def go(cl):
        assert (await cl.get("/api/state")).status == 401
        assert (await cl.get("/api/state", headers={"Authorization": "Bearer wrong"})).status == 401
        assert (await cl.get("/api/state", headers={"Authorization": "Bearer s3cret"})).status == 200
        assert (await cl.get("/api/state?token=s3cret")).status == 200
        assert (await cl.post("/api/alarms", json={"time": "07:00"})).status == 401
        assert (await cl.get("/")).status == 200  # UI shell itself is public

    run(go, tmp_path, token="s3cret")


def test_dismiss_refused_when_idle_or_disabled(tmp_path):
    async def go(cl):
        assert (await cl.post("/api/dismiss")).status == 403

    run(go, tmp_path)


def test_static_pages(tmp_path):
    async def go(cl):
        r = await cl.get("/")
        assert r.status == 200 and "Alarm+" in await r.text()
        r = await cl.get("/sound/alarm.wav")
        assert r.status == 200 and (await r.read())[:4] == b"RIFF"

    run(go, tmp_path)


def test_sse_first_event_is_snapshot(tmp_path):
    async def go(cl):
        await cl.post("/api/alarms", json={"time": "07:00"})
        r = await cl.get("/api/events")
        line = await asyncio.wait_for(r.content.readline(), 3)
        r.close()
        return json.loads(line.removeprefix(b"data: "))

    snap = run(go, tmp_path)
    assert snap["state"] == "idle" and snap["next"]["at"].endswith("07:00")
