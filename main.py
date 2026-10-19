import json
import logging
import os
import time
from collections import deque
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Request, Response, Query, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
from gemini_agent import handle_incoming_message, clear_session, load_catalog_data, SESSION_STORAGE
from whatsapp_client import send_whatsapp_text, send_whatsapp_image, mark_message_as_read
from woocommerce_client import is_woocommerce_configured
from order_manager import ACTIVE_ORDERS

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("PlayStationWorldBot")

app = FastAPI(
    title="PlayStation World Multan - WhatsApp AI Agent",
    description="Intelligent AI Agent for PlayStation World Multan handling WhatsApp inquiries for games, consoles, and subscription codes.",
    version="1.0.0"
)

# Rolling buffer of recent bot activities / chat logs (up to 100 entries)
RECENT_LOGS = deque(maxlen=100)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def log_event(event_type: str, sender: str, text: str, reply: Optional[str] = None):
    RECENT_LOGS.appendleft({
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "type": event_type,
        "sender": sender,
        "text": text,
        "reply": reply
    })


def get_requested_qr_images(message_text: str) -> list:
    """Detect if customer asks for QR code and return list of (file_path, caption, url_path)."""
    msg = message_text.lower()
    results = []

    qr_triggers = ["qr", "qr code", "bar code", "barcode", "scan", "scanner"]
    if any(t in msg for t in qr_triggers):
        ep_path = STATIC_DIR / "qr" / "easypaisa_qr.jpg"
        jc_path = STATIC_DIR / "qr" / "jazzcash_qr.jpg"

        want_ep = "easypaisa" in msg or "easy paisa" in msg or "paisa" in msg
        want_jc = "jazzcash" in msg or "jazz cash" in msg or "jazz" in msg

        if (want_ep and want_jc) or (not want_ep and not want_jc):
            if jc_path.exists():
                results.append((str(jc_path), "JazzCash QR Code - Junaid Fiaz (03054149002)", "/static/qr/jazzcash_qr.jpg"))
            if ep_path.exists():
                results.append((str(ep_path), "Easypaisa QR Code - Junaid Fiaz (03467146530)", "/static/qr/easypaisa_qr.jpg"))
        elif want_jc and jc_path.exists():
            results.append((str(jc_path), "JazzCash QR Code - Junaid Fiaz (03054149002)", "/static/qr/jazzcash_qr.jpg"))
        elif want_ep and ep_path.exists():
            results.append((str(ep_path), "Easypaisa QR Code - Junaid Fiaz (03467146530)", "/static/qr/easypaisa_qr.jpg"))

    return results


def process_whatsapp_message_task(sender_phone: str, message_text: str, message_id: str, sender_name: str = "Customer"):
    """
    Background worker that runs the Gemini AI model and sends the reply via WhatsApp Cloud API.
    Executed asynchronously so Meta webhook receives 200 OK immediately without timeouts.
    """
    try:
        logger.info(f"Processing incoming WhatsApp message from {sender_name} ({sender_phone}): {message_text}")
        
        # 1. Mark message as read (shows blue tick to customer)
        if message_id:
            mark_message_as_read(message_id)

        # 2. Generate intelligent response using Gemini Agent (includes WC Order integration)
        ai_reply = handle_incoming_message(sender_phone, message_text, customer_name=sender_name)
        logger.info(f"Generated AI reply for {sender_phone}: {ai_reply[:80]}...")

        # 3. Send text reply to WhatsApp
        send_res = send_whatsapp_text(sender_phone, ai_reply)

        # 4. If customer requested QR code, send the QR code image(s) as well!
        qr_images = get_requested_qr_images(message_text)
        for img_path, caption, _ in qr_images:
            logger.info(f"Sending QR code image ({caption}) to {sender_phone}...")
            send_whatsapp_image(sender_phone, img_path, caption=caption)

        # 5. Record event log
        log_event("whatsapp_incoming", sender_phone, message_text, ai_reply)

    except Exception as e:
        logger.error(f"Error processing message from {sender_phone}: {e}", exc_info=True)
        log_event("error", sender_phone, message_text, f"Error: {e}")


# =====================================================================
# META WHATSAPP CLOUD API WEBHOOKS
# =====================================================================

@app.get("/webhook")
async def verify_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    """
    Meta Webhook Verification Endpoint.
    When configuring the webhook in Meta App Dashboard, Meta sends a GET request
    with hub.mode=subscribe and hub.verify_token to verify ownership.
    """
    logger.info(f"Webhook verification request received. Mode: {hub_mode}")

    if hub_mode == "subscribe" and hub_verify_token == config.WHATSAPP_VERIFY_TOKEN:
        logger.info("Webhook verified successfully!")
        return Response(content=hub_challenge, media_type="text/plain")

    logger.warning("Webhook verification failed: token mismatch or invalid mode.")
    raise HTTPException(status_code=403, detail="Verification token mismatch")


@app.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Meta Webhook Event Receiver.
    Receives incoming WhatsApp messages and notifications.
    Immediately returns 200 OK to satisfy Meta's latency requirements,
    and delegates AI inference & replying to background tasks.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "invalid json"}, status_code=400)

    # Check for WhatsApp message entry
    entry = body.get("entry", [])
    for e in entry:
        changes = e.get("changes", [])
        for change in changes:
            value = change.get("value", {})
            messages = value.get("messages", [])
            contacts = value.get("contacts", [])

            sender_name = contacts[0].get("profile", {}).get("name", "Customer") if contacts else "Customer"

            for msg in messages:
                msg_type = msg.get("type")
                sender_phone = msg.get("from")
                msg_id = msg.get("id")

                # Text messages
                if msg_type == "text":
                    text_body = msg.get("text", {}).get("body", "").strip()
                    if text_body and sender_phone:
                        background_tasks.add_task(
                            process_whatsapp_message_task,
                            sender_phone,
                            text_body,
                            msg_id,
                            sender_name
                        )

                # Interactive button replies or list selections
                elif msg_type == "interactive":
                    interactive = msg.get("interactive", {})
                    itype = interactive.get("type")
                    selected_text = ""
                    if itype == "button_reply":
                        selected_text = interactive.get("button_reply", {}).get("title", "")
                    elif itype == "list_reply":
                        selected_text = interactive.get("list_reply", {}).get("title", "")
                    
                    if selected_text and sender_phone:
                        background_tasks.add_task(
                            process_whatsapp_message_task,
                            sender_phone,
                            selected_text,
                            msg_id,
                            sender_name
                        )

    return JSONResponse({"status": "received"})


# =====================================================================
# REST APIS FOR DASHBOARD & SIMULATOR
# =====================================================================

@app.get("/")
async def root():
    """Serve the Web Dashboard / WhatsApp Simulator."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse("<h1>PlayStation World Multan - AI Bot Server Running</h1>")


@app.get("/api/status")
async def get_status():
    """Return health & configuration status."""
    catalog = load_catalog_data()
    total_subscriptions = len(catalog.get("subscriptions", []))
    total_games = len(catalog.get("games", []))
    total_hardware = len(catalog.get("hardware_and_accessories", []))

    return {
        "shop_name": config.SHOP_NAME,
        "gemini_configured": bool(config.GEMINI_API_KEY),
        "gemini_model": config.GEMINI_MODEL,
        "whatsapp_configured": bool(config.WHATSAPP_TOKEN and config.WHATSAPP_PHONE_NUMBER_ID),
        "whatsapp_verify_token": config.WHATSAPP_VERIFY_TOKEN,
        "woocommerce_configured": is_woocommerce_configured(),
        "active_orders_count": len(ACTIVE_ORDERS),
        "active_chat_sessions": len(SESSION_STORAGE),
        "catalog_stats": {
            "subscriptions": total_subscriptions,
            "games": total_games,
            "hardware": total_hardware
        }
    }


class TestChatRequest(BaseModel):
    session_id: str = "simulator-test-user"
    customer_name: Optional[str] = "Customer"
    message: str


@app.post("/api/chat/test")
async def test_chat(req: TestChatRequest):
    """
    Test chat endpoint for the web-based WhatsApp Simulator.
    Allows testing the agent directly from the dashboard!
    """
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    reply = handle_incoming_message(req.session_id, req.message, customer_name=req.customer_name or "Customer")
    log_event("web_simulator", req.session_id, req.message, reply)

    qr_imgs = [img[2] for img in get_requested_qr_images(req.message)]

    return {
        "session_id": req.session_id,
        "user_message": req.message,
        "reply": reply,
        "qr_images": qr_imgs
    }


@app.post("/api/chat/reset")
async def reset_chat_session(session_id: str = Query("simulator-test-user")):
    """Clear conversation history for a given session."""
    clear_session(session_id)
    return {"status": "cleared", "session_id": session_id}


@app.get("/api/catalog")
async def get_catalog():
    """Fetch current catalog."""
    return load_catalog_data()


@app.post("/api/catalog")
async def update_catalog(request: Request):
    """Save updated catalog."""
    new_data = await request.json()
    with open(config.CATALOG_PATH, "w", encoding="utf-8") as f:
        json.dump(new_data, f, indent=2, ensure_ascii=False)
    return {"status": "success", "message": "Catalog updated successfully"}


@app.post("/api/catalog/sync")
async def sync_catalog_endpoint():
    """Trigger background re-sync of products & prices from playstationworld.pk."""
    try:
        import sync_website_catalog
        sync_website_catalog.main()
        return {"status": "success", "message": "Catalog synced successfully from playstationworld.pk!"}
    except Exception as e:
        logger.error(f"Error syncing catalog: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/logs")
async def get_logs():
    """Retrieve recent conversation logs."""
    return list(RECENT_LOGS)


class SettingsPayload(BaseModel):
    gemini_api_key: Optional[str] = None
    gemini_model: Optional[str] = None
    whatsapp_token: Optional[str] = None
    whatsapp_phone_number_id: Optional[str] = None
    whatsapp_verify_token: Optional[str] = None
    woocommerce_url: Optional[str] = None
    wc_consumer_key: Optional[str] = None
    wc_consumer_secret: Optional[str] = None


@app.post("/api/settings")
async def update_settings(payload: SettingsPayload):
    """Update environment variables and save to .env."""
    env_path = BASE_DIR / ".env"
    
    current_env = {}
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    current_env[k.strip()] = v.strip()

    if payload.gemini_api_key is not None:
        current_env["GEMINI_API_KEY"] = payload.gemini_api_key
        config.GEMINI_API_KEY = payload.gemini_api_key

    if payload.gemini_model is not None:
        current_env["GEMINI_MODEL"] = payload.gemini_model
        config.GEMINI_MODEL = payload.gemini_model

    if payload.whatsapp_token is not None:
        current_env["WHATSAPP_TOKEN"] = payload.whatsapp_token
        config.WHATSAPP_TOKEN = payload.whatsapp_token

    if payload.whatsapp_phone_number_id is not None:
        current_env["WHATSAPP_PHONE_NUMBER_ID"] = payload.whatsapp_phone_number_id
        config.WHATSAPP_PHONE_NUMBER_ID = payload.whatsapp_phone_number_id

    if payload.whatsapp_verify_token is not None:
        current_env["WHATSAPP_VERIFY_TOKEN"] = payload.whatsapp_verify_token
        config.WHATSAPP_VERIFY_TOKEN = payload.whatsapp_verify_token

    if payload.woocommerce_url is not None:
        current_env["WOOCOMMERCE_URL"] = payload.woocommerce_url
        config.WOOCOMMERCE_URL = payload.woocommerce_url

    if payload.wc_consumer_key is not None:
        current_env["WC_CONSUMER_KEY"] = payload.wc_consumer_key
        config.WC_CONSUMER_KEY = payload.wc_consumer_key

    if payload.wc_consumer_secret is not None:
        current_env["WC_CONSUMER_SECRET"] = payload.wc_consumer_secret
        config.WC_CONSUMER_SECRET = payload.wc_consumer_secret

    # Write back to .env
    with open(env_path, "w", encoding="utf-8") as f:
        for k, v in current_env.items():
            f.write(f"{k}={v}\n")

    return {"status": "success", "message": "Settings updated and saved to .env"}
