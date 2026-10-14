import asyncio
import ipaddress
import json
import sys

from aiohttp import web
from server import PromptServer

NODE_CLASS_MAPPINGS = {}

if sys.platform == "win32":
    WEB_DIRECTORY = "./web"

    from .connector import Connector
    import nodes

    connector = Connector()

    def save_images(self, images, filename_prefix="ComfyUI", prompt=None, extra_pnginfo=None):
        return connector.capture(self, images, filename_prefix, prompt, extra_pnginfo)

    nodes.SaveImage.save_images = save_images

    def local_request(request):
        if not request.remote or not ipaddress.ip_address(request.remote).is_loopback:
            raise web.HTTPForbidden(text=json.dumps({"error": "Open ComfyUI through localhost to use PureRef."}), content_type="application/json")

    @PromptServer.instance.routes.get("/pureref_connector/settings")
    async def get_settings(request):
        local_request(request)
        return web.json_response(connector.snapshot())

    @PromptServer.instance.routes.post("/pureref_connector/settings")
    async def set_settings(request):
        local_request(request)
        try:
            values = await request.json()
            result = await asyncio.to_thread(connector.update, values)
            return web.json_response(result)
        except (ValueError, OSError) as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @PromptServer.instance.routes.post("/pureref_connector/open")
    async def open_board(request):
        local_request(request)
        try:
            values = await request.json()
            result = await asyncio.to_thread(connector.open_board, values)
            return web.json_response(result)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
