"""Tiny Home Assistant websocket client for registry/admin calls the REST API lacks.

Usage: python tools/haws.py '{"type":"config/area_registry/list"}' ['<json cmd>' ...]
Needs the `websockets` package (e.g. a throwaway venv). Token is read from
~/.config/home-assistant/claude-token (mode 600, created by the user) and never printed.
"""
Each command is a JSON object without "id"; results printed as JSON lines. Token read from file, never printed."""
import asyncio, json, os, sys
import websockets
URL = "ws://192.168.0.67:20810/api/websocket"
TOKEN = open(os.path.expanduser("~/.config/home-assistant/claude-token")).read().strip()
async def main(cmds):
    async with websockets.connect(URL, max_size=64 * 1024 * 1024) as ws:
        assert json.loads(await ws.recv())["type"] == "auth_required"
        await ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
        r = json.loads(await ws.recv())
        if r["type"] != "auth_ok": sys.exit(f"auth failed: {r.get('message')}")
        for i, c in enumerate(cmds, start=1):
            c = dict(c, id=i)
            await ws.send(json.dumps(c))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == i: break
            print(json.dumps({"cmd": c["type"], "success": r.get("success"), "result": r.get("result"), "error": r.get("error")}))
asyncio.run(main([json.loads(a) for a in sys.argv[1:]]))
