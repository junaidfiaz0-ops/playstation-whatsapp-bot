import json
import logging
import re
from pathlib import Path
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
ORDERS_FILE = BASE_DIR / "active_orders.json"

# In-memory store: user_phone -> {"order_id": str, "product_id": int, "title": str, "total": str, "status": str, "created_at": float}
ACTIVE_ORDERS: Dict[str, dict] = {}


def clean_user_id(user_id: str) -> str:
    """Normalize phone number / user session id."""
    return re.sub(r'[^a-zA-Z0-9]', '', str(user_id).strip())


def load_orders():
    global ACTIVE_ORDERS
    if ORDERS_FILE.exists():
        try:
            with open(ORDERS_FILE, "r", encoding="utf-8") as f:
                ACTIVE_ORDERS = json.load(f)
        except Exception as e:
            logger.error(f"Error loading active_orders.json: {e}")
            ACTIVE_ORDERS = {}


def save_orders():
    try:
        with open(ORDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(ACTIVE_ORDERS, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving active_orders.json: {e}")


# Initialize orders on module import
load_orders()


def get_active_order(user_id: str) -> Optional[dict]:
    """Retrieve active order for customer."""
    cid = clean_user_id(user_id)
    return ACTIVE_ORDERS.get(cid)


def set_active_order(user_id: str, order_id: str, product_id: Optional[int], title: str, total: str, status: str = "pending"):
    """Store or update active order for customer."""
    import time
    cid = clean_user_id(user_id)
    ACTIVE_ORDERS[cid] = {
        "order_id": str(order_id),
        "product_id": product_id,
        "title": title,
        "total": str(total),
        "status": status,
        "updated_at": time.time()
    }
    save_orders()


def clear_active_order(user_id: str):
    """Remove active order after completion or cancellation."""
    cid = clean_user_id(user_id)
    if cid in ACTIVE_ORDERS:
        del ACTIVE_ORDERS[cid]
        save_orders()


def is_payment_confirmation(text: str) -> bool:
    """
    Check if the user is confirming that they made the payment.
    Handles ANY payment method: Bank Alfalah, Raast, JazzCash, EasyPaisa, SadaPay, NayaPay, or QR Code.
    """
    t = text.lower().strip()
    
    # Specific affirmative phrases across English, Roman Urdu, and slang
    patterns = [
        # Explicit payment verbs & status
        r"\b(paid|already\s*paid|payment\s*done|payment\s*sent|payment\s*made|payment\s*completed)\b",
        r"\b(done\s*payment|amount\s*paid|money\s*sent|funds\s*transferred|transfer\s*done)\b",
        r"\b(screenshot\s*sent|screenshot\s*bhej\s*(di|diya|diye|dya))\b",
        
        # Method-specific confirmations (with or without QR)
        r"\b(qr|bank|raast|jazzcash|jazz\s*cash|easypaisa|easy\s*paisa|sadapay|nayapay|account)\b.*(pay|paid|transfer|trf|send|sent|bhej|kar|kr|done)",
        r"(pay|paid|transfer|trf|send|sent|bhej|kar|kr)\b.*(qr|bank|raast|jazzcash|jazz\s*cash|easypaisa|easy\s*paisa|sadapay|nayapay|account)",
        
        # Roman Urdu / Hindi confirmations
        r"\b(pay|payment)\s*(kar|kr)\s*(di|diya|diye|dya|dia)\b",
        r"\b(transfer|trf)\s*(kar|kr)\s*(di|diya|diye|dya|dia|done)\b",
        r"\b(paise|pesay|pese|amount)\s*(bhej|send|transfer|kar|kr)\s*(di|diya|diye|dya|dia)\b",
        r"\b(bhej|bheej|send)\s*(di|diya|diye|dya|dia|dien|di hai|diya hai|diye hain)\b",
        r"\b(kar|kr)\s*(di|diya|diye|dya|dia|di hai|diya hai|diye hain)\b",
        r"\b(ho\s*gaya|ho\s*gya|ho\s*gayi|ho\s*gyi|ho\s*gaya\s*hai|ho\s*gya\s*hai)\b",
        r"\b(bayan|ada)\s*(kar|kr)\s*(di|diya|diye|dya)\b",
        r"\b(yes\s*paid|ha\s*paid|haan\s*paid|ji\s*paid)\b",
        
        # Direct standalone confirmation words
        r"^(paid|done|yes|haan|ha|ji|ok done|ho gaya|kar diya|bhej diya|kar di|bhej di|sent)$"
    ]
    for pattern in patterns:
        if re.search(pattern, t):
            return True

    return False



def is_purchase_intent(text: str) -> bool:
    """Check if user wants to buy or place an order or asks for payment details."""
    t = text.lower()
    intent_patterns = [
        r"\b(buy|order|purchase|checkout)\b",
        r"\b(lena|leni|khareedna|kharidna|mangwana|mangwani)\b",
        r"\b(chahiye)\b",
        r"\b(payment|account|jazzcash|easypaisa|raast|bank)\s*(detail|details|number|karni|bhejo|send)\b",
        r"\b(qr|qr\s*code|barcode|scanner)\b",
        r"\b(how\s*to\s*buy|kese\s*buy|kaise\s*buy|kaise\s*order)\b"
    ]
    for p in intent_patterns:
        if re.search(p, t):
            return True
    return False


def find_best_product_match(query: str, catalog: dict) -> Optional[dict]:
    """Find the single most relevant catalog item matching the user's inquiry."""
    query_lower = query.lower()
    # Extract words and tokens (alphanumeric)
    words = re.findall(r'[a-zA-Z0-9]+', query_lower)
    stop_words = {
        "bhai", "hai", "mujhe", "chahiye", "lena", "leni", "buy", "order", 
        "purchase", "karna", "karni", "send", "details", "qr", "code", "payment",
        "please", "the", "for", "and", "with", "a", "an", "in", "to", "ka", "ki", 
        "ke", "ko", "se", "par", "hoga", "karo", "do", "bhejo", "yes", "no"
    }
    keywords = [w for w in words if (len(w) >= 2 or w.isdigit()) and w not in stop_words]

    if not keywords:
        return None

    all_items = (
        catalog.get("subscriptions", []) +
        catalog.get("games", []) +
        catalog.get("hardware_and_accessories", [])
    )

    best_item = None
    best_score = 0

    for item in all_items:
        title = item.get("title", "").lower()
        cats = " ".join(item.get("categories", [])).lower()
        score = 0

        # Exact title substring match
        for kw in keywords:
            if kw in title:
                score += 5
            elif kw in cats:
                score += 2

        # Bonus if entire query title appears
        if " ".join(keywords) in title:
            score += 15

        if score > best_score:
            best_score = score
            best_item = item

    # Only return if we have a solid keyword match
    if best_score >= 5:
        return best_item
    return None


def find_best_product_match_with_history(current_query: str, history: list, catalog: dict) -> Optional[dict]:
    """
    Search for the product in the current message first; if not found,
    inspect the recent history to see what product the customer was inquiring about.
    """
    # 1. Check current message
    match = find_best_product_match(current_query, catalog)
    if match:
        return match

    # 2. Check recent history in reverse
    for turn in reversed(history[-6:]):
        text = turn.get("text", "")
        match = find_best_product_match(text, catalog)
        if match:
            return match

    return None
