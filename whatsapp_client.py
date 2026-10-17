import logging
from pathlib import Path
import requests
from config import WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_API_VERSION

logger = logging.getLogger(__name__)


def send_whatsapp_text(recipient_phone: str, text: str) -> dict:
    """
    Send a plain text message to a customer via Meta WhatsApp Cloud API.
    `recipient_phone` format: E.164 without '+' or standard (e.g. '923001234567').
    """
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        logger.warning("WhatsApp Token or Phone Number ID not configured. Message not sent via WhatsApp Cloud API.")
        return {"status": "skipped", "reason": "Missing WhatsApp credentials"}

    # Strip any leading '+' if present
    clean_phone = recipient_phone.replace("+", "").strip()

    url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": clean_phone,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": text
        }
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        data = response.json()
        if response.status_code == 200:
            logger.info(f"WhatsApp message successfully sent to {clean_phone}")
            return {"status": "success", "data": data}
        else:
            logger.error(f"WhatsApp Cloud API Error {response.status_code}: {data}")
            return {"status": "error", "code": response.status_code, "data": data}
    except Exception as e:
        logger.error(f"Failed to send WhatsApp message to {clean_phone}: {e}")
        return {"status": "exception", "error": str(e)}


def upload_whatsapp_media(file_path: str, mime_type: str = "image/jpeg") -> str:
    """
    Upload a local image file directly to WhatsApp Cloud API and return its media_id.
    """
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return ""

    url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/media"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}"
    }

    try:
        with open(file_path, "rb") as f:
            files = {
                "file": (Path(file_path).name, f, mime_type),
                "messaging_product": (None, "whatsapp"),
                "type": (None, mime_type)
            }
            res = requests.post(url, headers=headers, files=files, timeout=25)
            if res.status_code == 200:
                media_id = res.json().get("id", "")
                logger.info(f"Successfully uploaded media {file_path} -> media_id: {media_id}")
                return media_id
            else:
                logger.error(f"Error uploading media to WhatsApp {res.status_code}: {res.text}")
    except Exception as e:
        logger.error(f"Failed to upload media file {file_path}: {e}")
    return ""


def send_whatsapp_image(recipient_phone: str, image_source: str, caption: str = "") -> dict:
    """
    Send an image message to customer.
    `image_source` can be a local file path (uploaded automatically) or a public URL.
    """
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return {"status": "skipped", "reason": "Missing WhatsApp credentials"}

    clean_phone = recipient_phone.replace("+", "").strip()
    url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": clean_phone,
        "type": "image"
    }

    # If it's a local file that exists on disk, upload to get media_id
    local_path = Path(image_source)
    if local_path.exists() and local_path.is_file():
        media_id = upload_whatsapp_media(str(local_path))
        if media_id:
            payload["image"] = {
                "id": media_id,
                "caption": caption
            }
        else:
            return send_whatsapp_text(clean_phone, f"{caption}\n(QR Code available on shop WhatsApp: 03054149002)")
    else:
        # Public URL
        payload["image"] = {
            "link": image_source,
            "caption": caption
        }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        data = response.json()
        if response.status_code == 200:
            logger.info(f"WhatsApp image sent to {clean_phone}")
            return {"status": "success", "data": data}
        else:
            logger.error(f"WhatsApp Cloud API Error sending image: {data}")
            return {"status": "error", "code": response.status_code, "data": data}
    except Exception as e:
        logger.error(f"Failed to send WhatsApp image to {clean_phone}: {e}")
        return {"status": "exception", "error": str(e)}


def mark_message_as_read(message_id: str) -> bool:
    """Mark an incoming WhatsApp message as read (blue tick)."""
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return False

    url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id
    }

    try:
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        return res.status_code == 200
    except Exception as e:
        logger.warning(f"Could not mark message {message_id} as read: {e}")
        return False
