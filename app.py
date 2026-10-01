"""
app.py
Hugging Face Spaces entry point for Ginchiee Bot.
Runs a minimal Python HTTP server on port 7860 to keep the Space alive,
while running the Telegram bot in the background via asyncio.
"""

import os
import sys
import asyncio
import logging
import threading

import gradio as gr
import spaces

from main import build_app

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("app")
bot_thread: threading.Thread | None = None


@spaces.GPU
def zero_gpu_runtime_probe() -> str:
    """Provide the callback ZeroGPU requires without using GPU for Telegram polling."""
    return "ZeroGPU runtime is registered. Telegram polling does not require GPU."


def bot_worker_status() -> str:
    if bot_thread and bot_thread.is_alive():
        return "Telegram polling worker is active."
    return "Telegram polling worker is not running. Check the Space logs."


# ── Telegram Bot Worker ────────────────────────────────────────────────────────
def start_bot_worker():
    logger.info("Starting Telegram bot worker thread...")
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        app = build_app()
        app.run_polling(stop_signals=None, drop_pending_updates=False)
    except Exception as e:
        logger.error(f"FATAL: Telegram bot crashed: {e}", exc_info=True)


with gr.Blocks(title="Ginchiee Bot") as demo:
    gr.Markdown("# Ginchiee Bot\nTelegram AI Health & Diet Companion")
    worker_status = gr.Textbox(
        label="Telegram worker",
        value="Telegram polling worker is starting.",
        interactive=False,
    )
    refresh_status = gr.Button("Refresh worker status")
    refresh_status.click(bot_worker_status, outputs=worker_status, api_name=False)

    # The callback is hidden because it only exists to register the ZeroGPU runtime.
    zero_gpu_trigger = gr.Button(visible=False)
    zero_gpu_status = gr.Textbox(visible=False)
    zero_gpu_trigger.click(zero_gpu_runtime_probe, outputs=zero_gpu_status, api_name=False)


if __name__ == "__main__":
    bot_thread = threading.Thread(target=start_bot_worker, daemon=True)
    bot_thread.start()

    port = int(os.getenv("PORT", "7860"))
    logger.info(f"Starting Gradio status page on port {port}...")
    demo.launch(server_name="0.0.0.0", server_port=port, show_error=True)
