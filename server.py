import os
import logging
from aiohttp import web
import config

logger = logging.getLogger("BayseBot.Server")

class DashboardServer:
    def __init__(self, bot):
        self.bot = bot
        self.port = config.DASHBOARD_PORT
        self.runner = None
        self.site = None

    async def start(self):
        app = web.Application()
        
        # Setup route mappings
        app.router.add_get("/api/status", self.handle_status)
        
        # Serve frontend dashboard static files
        static_path = os.path.join(os.path.dirname(__file__), "dashboard")
        if os.path.exists(static_path):
            async def handle_index(request):
                return web.FileResponse(os.path.join(static_path, "index.html"))
            app.router.add_get("/", handle_index)
            app.router.add_static("/", static_path)
            logger.info(f"Serving dashboard static files from {static_path}")
        else:
            logger.warning(f"Dashboard directory not found at {static_path}. API routes are active.")

        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", self.port)
        await self.site.start()
        logger.info(f"Dashboard web server started and listening on http://127.0.0.1:{self.port}")

    async def stop(self):
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        logger.info("Dashboard web server stopped.")

    async def handle_status(self, request):
        """
        API Endpoint returning current wallet balance, active evaluations, kill-switch state, and log buffer.
        """
        try:
            status_data = await self.bot.get_bot_status()
            return web.json_response(status_data)
        except Exception as e:
            logger.error(f"Error handling status request: {e}")
            return web.json_response({"error": str(e)}, status=500)
