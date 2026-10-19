import logging
import os
import time
from pathlib import Path
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

logger = logging.getLogger(__name__)


def get_wc_credentials():
    """Retrieve up-to-date WooCommerce credentials from config / environment."""
    import config
    url = getattr(config, "WOOCOMMERCE_URL", os.getenv("WOOCOMMERCE_URL", "https://playstationworld.pk")).rstrip("/")
    key = getattr(config, "WC_CONSUMER_KEY", os.getenv("WC_CONSUMER_KEY", "")).strip()
    secret = getattr(config, "WC_CONSUMER_SECRET", os.getenv("WC_CONSUMER_SECRET", "")).strip()
    return url, key, secret


def is_woocommerce_configured() -> bool:
    """Check if WooCommerce credentials are fully configured."""
    _, key, secret = get_wc_credentials()
    return bool(key and secret)


def create_wc_order(customer_phone: str, customer_name: str = "WhatsApp Customer", product_id: int = None, quantity: int = 1, product_title: str = None, price: float = None):
    """
    Create a live pending order on WooCommerce website via REST API.
    Returns (order_id, total_amount) from WooCommerce, or (None, None) if error occurs.
    """
    url, key, secret = get_wc_credentials()
    clean_phone = customer_phone.replace("+", "").replace("-", "").replace(" ", "").strip()

    if not (key and secret):
        logger.error(
            f"Cannot create live WooCommerce order: WC_CONSUMER_KEY and WC_CONSUMER_SECRET are not configured in .env! "
            f"Please configure your WooCommerce REST API keys in .env to place orders on {url}."
        )
        return None, None

    api_url = f"{url}/wp-json/wc/v3/orders"
    auth = HTTPBasicAuth(key, secret)

    line_items = []
    if product_id:
        line_items.append({
            "product_id": int(product_id),
            "quantity": quantity
        })
    elif product_title:
        line_items.append({
            "name": product_title,
            "quantity": quantity,
            "total": str(price or "0")
        })

    order_data = {
        "payment_method": "bacs",
        "payment_method_title": "JazzCash / EasyPaisa / Bank Transfer",
        "set_paid": False,
        "status": "pending",
        "billing": {
            "first_name": customer_name or "WhatsApp Customer",
            "phone": clean_phone,
            "email": f"{clean_phone}@whatsapp.com"
        },
        "line_items": line_items,
        "customer_note": f"Order placed via WhatsApp AI Agent for {clean_phone}"
    }

    try:
        logger.info(f"Sending POST request to {api_url} to create order for {clean_phone} (Product ID: {product_id})...")
        response = requests.post(api_url, json=order_data, auth=auth, timeout=15)
        if response.status_code == 201:
            data = response.json()
            order_id = str(data.get("id"))
            total = str(data.get("total", price or "0"))
            logger.info(f"✅ Successfully created WooCommerce order #{order_id} on {url} for {clean_phone} (Total: PKR {total})")
            return order_id, total
        else:
            logger.error(f"❌ WooCommerce API Order Error (HTTP {response.status_code}): {response.text}")
            return None, None
    except Exception as e:
        logger.error(f"❌ Exception connecting to WooCommerce API ({api_url}): {e}")
        return None, None


def update_order_to_processing(order_id: str) -> bool:
    """
    Update a WooCommerce order status from pending to processing upon customer payment confirmation via REST API.
    """
    url, key, secret = get_wc_credentials()

    if not (key and secret):
        logger.error("Cannot update WooCommerce order: WC_CONSUMER_KEY or WC_CONSUMER_SECRET not configured in .env!")
        return False

    api_url = f"{url}/wp-json/wc/v3/orders/{order_id}"
    auth = HTTPBasicAuth(key, secret)

    update_data = {
        "status": "processing"
    }

    try:
        logger.info(f"Sending PUT request to {api_url} to update order #{order_id} status to 'processing'...")
        response = requests.put(api_url, json=update_data, auth=auth, timeout=15)
        if response.status_code == 200:
            logger.info(f"✅ WooCommerce order #{order_id} successfully marked as 'processing' on {url}.")
            return True
        else:
            logger.error(f"❌ WooCommerce Update Error (HTTP {response.status_code}): {response.text}")
            return False
    except Exception as e:
        logger.error(f"❌ Exception connecting to WooCommerce API ({api_url}): {e}")
        return False
