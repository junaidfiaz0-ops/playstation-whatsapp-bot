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
    existing = ACTIVE_ORDERS.get(cid, {})
    last_prod = existing.get("last_discussed_product")
    ACTIVE_ORDERS[cid] = {
        "order_id": str(order_id),
        "product_id": product_id,
        "title": title,
        "total": str(total),
        "status": status,
        "updated_at": time.time(),
        "last_discussed_product": last_prod or {
            "id": product_id,
            "title": title,
            "price_pkr": total
        }
    }
    save_orders()


def set_last_discussed_product(user_id: str, product: dict):
    """Store the product currently being inquired about or discussed."""
    cid = clean_user_id(user_id)
    if cid not in ACTIVE_ORDERS:
        ACTIVE_ORDERS[cid] = {}
    ACTIVE_ORDERS[cid]["last_discussed_product"] = {
        "id": product.get("id"),
        "title": product.get("title"),
        "price_pkr": product.get("price_pkr", 0)
    }
    save_orders()


def get_last_discussed_product(user_id: str) -> Optional[dict]:
    """Retrieve the last product discussed with the customer."""
    cid = clean_user_id(user_id)
    entry = ACTIVE_ORDERS.get(cid)
    if entry and isinstance(entry, dict):
        return entry.get("last_discussed_product")
    return None



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
        r"\b(lena|leni|lene|khareedna|kharidna|kharidni|mangwana|mangwani)\b",
        r"\b(chahiye|chaheay)\b",
        r"\b(payment|account|jazzcash|easypaisa|raast|bank)\s*(detail|details|number|karni|bhejo|send)\b",
        r"\b(qr|qr\s*code|barcode|scanner)\b",
        r"\b(how\s*to\s*buy|kese\s*buy|kaise\s*buy|kaise\s*order)\b",
        r"\b(place\s*order|order\s*this|buy\s*this|order\s*it|buy\s*it)\b"
    ]
    for p in intent_patterns:
        if re.search(p, t):
            return True
    return False


GENERIC_STOP_WORDS = {
    'please', 'give', 'me', 'price', 'rate', 'cost', 'how', 'much', 'is', 'for', 
    'the', 'a', 'an', 'and', 'or', 'with', 'in', 'to', 'at', 'from', 'of', 'on', 
    'this', 'that', 'these', 'those', 'it', 'its', 'item', 'product', 'products', 
    'one', 'ones', 'place', 'order', 'buy', 'purchase', 'checkout', 'get', 'need', 
    'want', 'send', 'details', 'qr', 'code', 'payment', 'yes', 'no', 'ok', 'okay',
    'can', 'you', 'i', 'we', 'my', 'your', 'sir', 'bro', 'bhai', 'game', 'games',
    'account', 'key', 'card', 'digital', 'batao', 'bata', 'bhai', 'hai', 'hain', 'ha',
    'mujhe', 'mujy', 'mjhe', 'chahiye', 'chaheay', 'lena', 'leni', 'lene',
    'khareedna', 'kharidna', 'kharidni', 'mangwana', 'mangwani',
    'karna', 'karni', 'karne', 'karo', 'kardo', 'kardein', 'karein',
    'yeh', 'ye', 'yehi', 'woh', 'wo', 'wohi', 'is', 'isko', 'iskay', 'iski',
    'us', 'usko', 'uskay', 'uski', 'wahi', 'same', 'wala', 'wali', 'wale',
    'ka', 'ki', 'ke', 'ko', 'se', 'par', 'pe', 'hoga', 'hogi', 'bhejo', 'bhej', 'dein', 'do',
    'kia', 'kya', 'kitne', 'kitna', 'kitni'
}


def clean_token(w: str) -> str:
    """Normalize word endings (plural vs singular)."""
    w = w.lower().strip()
    if w.endswith('months'): return 'month'
    if w.endswith('month'): return 'month'
    if w.endswith('years'): return 'year'
    if w.endswith('days'): return 'day'
    if w.endswith('editions'): return 'edition'
    if w.endswith('games'): return 'game'
    return w


def find_best_product_match(query: str, catalog: dict) -> Optional[dict]:
    """Find the single most relevant catalog item matching the user's inquiry."""
    raw_words = re.findall(r'[a-zA-Z0-9]+', query.lower())
    words = [clean_token(w) for w in raw_words if clean_token(w) not in GENERIC_STOP_WORDS]
    if not words:
        return None

    all_items = (
        catalog.get('subscriptions', []) +
        catalog.get('games', []) +
        catalog.get('hardware_and_accessories', [])
    )

    scored = []
    for item in all_items:
        title = item.get('title', '').lower()
        title_tokens = [clean_token(w) for w in re.findall(r'[a-zA-Z0-9]+', title)]
        title_norm = ' ' + ' '.join(title_tokens) + ' '
        cats = ' '.join(item.get('categories', [])).lower()
        score = 0

        # Exact adjacent pairs match (e.g. '2 month', 'game pass')
        for i in range(len(words) - 1):
            two_words = f" {words[i]} {words[i+1]} "
            if two_words in title_norm:
                score += 30

        for w in words:
            pattern = r'\b' + re.escape(w) + r'\b'
            if re.search(pattern, title_norm):
                if w.isdigit():
                    score += 20
                else:
                    score += 8
            elif re.search(pattern, cats):
                score += 2

        if score >= 10:
            scored.append((score, item))

    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    return None


def find_best_product_match_with_history(current_query: str, history: list, catalog: dict, user_id: str = "") -> Optional[dict]:
    """
    Search for the product in the current message first.
    If the current message is vague or referential ('this product', 'buy it', 'yeh order kardo'):
    1. Check last_discussed_product in user state.
    2. Check recent history in reverse for user inquiries or assistant quoted titles.
    """
    # 1. Check if current message explicitly mentions a specific product
    match = find_best_product_match(current_query, catalog)
    if match:
        return match

    # 2. Check persistent state for last discussed product
    if user_id:
        last_prod = get_last_discussed_product(user_id)
        if last_prod:
            all_items = (
                catalog.get('subscriptions', []) +
                catalog.get('games', []) +
                catalog.get('hardware_and_accessories', [])
            )
            for item in all_items:
                if item.get("id") == last_prod.get("id"):
                    return item
            return last_prod

    # 3. Check recent history in reverse
    for turn in reversed(history[-6:]):
        text = turn.get("text", "")
        # If assistant message has *Title* format
        quoted_titles = re.findall(r'\*([^*]+)\*', text)
        for q_title in quoted_titles:
            match = find_best_product_match(q_title, catalog)
            if match:
                return match

        match = find_best_product_match(text, catalog)
        if match:
            return match

    return None

