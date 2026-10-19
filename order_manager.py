import json
import logging
import re
from pathlib import Path
from typing import Optional, Dict, List, Tuple

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
ORDERS_FILE = BASE_DIR / "active_orders.json"

# In-memory store: user_phone -> {"order_id": str, "product_id": int, "title": str, "total": str, "status": str, "created_at": float}
ACTIVE_ORDERS: Dict[str, dict] = {}


def clean_user_id(user_id: str) -> str:
    """Normalize phone number / user session id."""
    raw = re.sub(r'[^a-zA-Z0-9]', '', str(user_id).strip())
    if raw.startswith('923') and len(raw) == 12:
        return '0' + raw[2:]
    return raw


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
        "updated_at": time.time(),
        "last_discussed_product": {
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
    Strictly distinguishes requests to pay/receive QR code from actual completed payment confirmation.
    """
    t = text.lower().strip()
    
    # 1. Negative guard: If user is asking how to pay, expressing desire to pay, or asking for QR / account details
    # e.g. "i want to pay using qr code", "send qr code", "share qr codes", "can i pay with jazzcash", "qr code bhejo"
    intent_to_pay_patterns = [
        r"\b(want\s*to|wants\s*to|how\s*to|can\s*i|going\s*to)\s*(pay|transfer|send)\b",
        r"\b(send|share|give|need|bhejo|bhej\s*do|bhej\s*dein|dikhao|dikha\s*dein)\s*(me\s*)?(the\s*)?(qr|qr\s*code|barcode|account|details)\b",
        r"\b(qr|qr\s*code|barcode|account)\s*(bhejo|send|share|dein|do|chahiye)\b",
        r"\b(pay\s*karna|pay\s*karni|karna\s*hai|karni\s*hai|karna\s*chahta|karna\s*chahti|krna\s*hai)\b"
    ]
    for ip in intent_to_pay_patterns:
        if re.search(ip, t):
            return False

    # 2. Positive confirmation patterns: Explicit completed payment / past-tense actions only
    patterns = [
        # Explicit completed payment verbs & status in English
        r"\b(paid|already\s*paid|i\s*(have\s*)?paid|just\s*paid)\b",
        r"\bpayment\s*(done|sent|made|completed|successful|transferred)\b",
        r"\b(done\s*payment|amount\s*paid|money\s*sent|funds\s*transferred|transfer\s*done)\b",
        r"\b(screenshot\s*sent|sent\s*the\s*(money|payment|screenshot))\b",
        r"\bpaid\s*(via|through|using|to|with)\s*(qr|bank|raast|jazzcash|easypaisa|sadapay|nayapay|account)\b",
        
        # Method-specific completed transfer in Roman Urdu
        r"\b(qr|bank|raast|jazzcash|jazz\s*cash|easypaisa|easy\s*paisa|sadapay|nayapay|account)\s*(se|par|pe|me|mein)?\s*(kar|kr|bhej|send|transfer|pay)\s*(di|diya|diye|dya|dia|hai|hain)\b",
        
        # General Roman Urdu / Hindi completions
        r"\b(pay|payment)\s*(kar|kr)\s*(di|diya|diye|dya|dia|di\s*hai|diya\s*hai|diye\s*hain)\b",
        r"\b(transfer|trf)\s*(kar|kr)\s*(di|diya|diye|dya|dia|done|di\s*hai|diya\s*hai|diye\s*hain)\b",
        r"\b(paise|pesay|pese|amount)\s*(bhej|send|transfer|kar|kr)\s*(di|diya|diye|dya|dia|diye\s*hain)\b",
        r"\b(bhej|bheej|send)\s*(di|diya|diye|dya|dia|dien|di\s*hai|diya\s*hai|diye\s*hain)\b",
        r"\b(kar|kr)\s*(di|diya|diye|dya|dia|di\s*hai|diya\s*hai|diye\s*hain)\b",
        r"\b(ho\s*gaya|ho\s*gya|ho\s*gayi|ho\s*gyi|ho\s*gaya\s*hai|ho\s*gya\s*hai)\b",
        r"\b(bayan|ada)\s*(kar|kr)\s*(di|diya|diye|dya)\b",
        r"\b(yes\s*paid|ha\s*paid|haan\s*paid|ji\s*paid)\b",
        
        # Direct standalone confirmation words
        r"^(paid|done|yes\s*paid|done\s*payment|payment\s*done|ho\s*gaya|kar\s*diya|bhej\s*diya|kar\s*di|bhej\s*di|sent)$"
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


MODIFIER_WORDS = {
    # Editions
    'standard', 'deluxe', 'ultimate', 'definitive', 'gold', 'premium', 'special',
    'classic', 'classics', 'remastered', 'anniversary', 'bundle', 'collection',
    'crossgen', 'cross', 'gen', 'edition', 'editions', 'essential', 'extra',
    # Platforms
    'xbox', 'ps', 'ps4', 'ps5', 'playstation', 'pc', 'steam', 'switch', 'nintendo',
    'series', 'one', 'x', 's', 'version', 'console',
    # Delivery / Accounts
    'key', 'code', 'cdkey', 'cdk', 'account', 'acc', 'new', 'old', 'renew', 'digital', 'disc',
    # Units / Durations
    'month', 'months', 'year', 'years', 'day', 'days', '1m', '2m', '3m', '5m', '12m'
}

GENERIC_STOP_WORDS = {
    'please', 'plz', 'pls', 'plzz', 'give', 'me', 'price', 'rate', 'cost', 'how', 'much', 'is', 'for', 
    'the', 'a', 'an', 'and', 'or', 'with', 'in', 'to', 'at', 'from', 'of', 'on', 
    'this', 'that', 'these', 'those', 'it', 'its', 'item', 'product', 'products', 
    'one', 'ones', 'place', 'order', 'buy', 'purchase', 'checkout', 'get', 'need', 
    'want', 'send', 'details', 'qr', 'code', 'payment', 'yes', 'no', 'ok', 'okay',
    'can', 'you', 'i', 'we', 'my', 'your', 'sir', 'bro', 'bhai', 'game', 'games',
    'batao', 'bata', 'hai', 'hain', 'ha', 'mujhe', 'mujy', 'mjhe', 'chahiye', 'chaheay',
    'lena', 'leni', 'lene', 'khareedna', 'kharidna', 'kharidni', 'mangwana', 'mangwani',
    'karna', 'karni', 'karne', 'karo', 'kardo', 'kardein', 'karein',
    'yeh', 'ye', 'yehi', 'woh', 'wo', 'wohi', 'is', 'isko', 'iskay', 'iski',
    'us', 'usko', 'uskay', 'uski', 'wahi', 'same', 'wala', 'wali', 'wale',
    'ka', 'ki', 'ke', 'ko', 'se', 'par', 'pe', 'hoga', 'hogi', 'bhejo', 'bhej', 'dein', 'do',
    'kia', 'kya', 'kitne', 'kitna', 'kitni'
}

ROMAN_NUMERALS = {
    '1': 'i', '2': 'ii', '3': 'iii', '4': 'iv', '5': 'v',
    '6': 'vi', '7': 'vii', '8': 'viii', '9': 'ix', '10': 'x'
}
REV_ROMAN = {v: k for k, v in ROMAN_NUMERALS.items()}


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


def normalize_token(w: str) -> str:
    w = clean_token(w)
    if w in REV_ROMAN:
        return REV_ROMAN[w]
    return w


def detect_platform_intent(text: str) -> dict:
    t = text.lower()
    ps = bool(re.search(r'\b(ps|ps4|ps5|playstation|play\s*station|psn|sony)\b', t))
    xb = bool(re.search(r'\b(xbox|series\s*[xs]|one\s*[xs]|xbox\s*one|xb)\b', t))
    pc = bool(re.search(r'\b(pc|steam|windows)\b', t))
    return {'ps': ps, 'xbox': xb, 'pc': pc}


def detect_item_platform(item: dict) -> dict:
    t = (item.get('title', '') + ' ' + ' '.join(item.get('categories', []))).lower()
    ps = bool(re.search(r'\b(ps4|ps5|playstation|psn)\b', t))
    xb = bool(re.search(r'\b(xbox|series\s*[xs]|one\s*[xs]|one/series)\b', t))
    pc = bool(re.search(r'\b(pc|steam|windows)\b', t))
    return {'ps': ps, 'xbox': xb, 'pc': pc}


def detect_item_edition(item: dict) -> str:
    t = item.get('title', '').lower()
    for ed in ['definitive', 'ultimate', 'deluxe', 'standard', 'gold', 'premium', 'essential', 'extra']:
        if ed in t:
            return ed.title() + " Edition"
    return "Standard Edition"


def extract_franchise_from_title(title: str) -> str:
    """Extract root franchise/game name from a title by stripping modifiers and platform names."""
    clean = re.sub(r'\s*\(.*?\)', '', title)
    clean = re.sub(r'\b(Standard|Deluxe|Ultimate|Definitive|Gold|Premium|Essential|Extra|Classics?)\s*(Edition)?\b', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\b(Xbox\s*One(\s*Series\s*[XS])?|PS4|PS5|PlayStation|PC|Key|Cross-Gen\s*Bundle)\b', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'[\/|\\:–-]+', ' ', clean).strip()
    return re.sub(r'\s+', ' ', clean)


def score_product_match(words: list, item: dict, content_words: list = None) -> int:
    title = item.get('title', '').lower()
    title_raw_tokens = [clean_token(w) for w in re.findall(r'[a-zA-Z0-9]+', title)]
    title_tokens = [normalize_token(w) for w in title_raw_tokens]
    title_norm = ' ' + ' '.join(title_tokens) + ' '
    raw_norm = ' ' + ' '.join(title_raw_tokens) + ' '
    cats = ' '.join(item.get('categories', [])).lower()

    # Content-word safeguard: if user mentioned specific game name(s), at least one must match!
    if content_words:
        matched_content = False
        for c in content_words:
            nc = normalize_token(c)
            if re.search(r'\b' + re.escape(nc) + r'\b', title_norm) or re.search(r'\b' + re.escape(c) + r'\b', raw_norm) or re.search(r'\b' + re.escape(c) + r'\b', cats):
                matched_content = True
                break
        if not matched_content:
            return 0

    score = 0
    norm_words = [normalize_token(w) for w in words]
    for i in range(len(norm_words) - 1):
        if f" {norm_words[i]} {norm_words[i+1]} " in title_norm or f" {words[i]} {words[i+1]} " in raw_norm:
            score += 35

    for w, nw in zip(words, norm_words):
        pat1 = r'\b' + re.escape(nw) + r'\b'
        pat2 = r'\b' + re.escape(w) + r'\b'
        if re.search(pat1, title_norm) or re.search(pat2, raw_norm):
            score += 20 if nw.isdigit() else 10
        elif re.search(pat2, cats):
            score += 4

    return score


def find_product_or_ambiguity(
    query: str,
    history: list,
    catalog: dict,
    user_id: str = ""
) -> Tuple[Optional[dict], bool, List[dict], str]:
    """
    Generalized resolver for ANY product, platform, or edition inquiry:
    1. Checks if the inquiry has multiple platform variants (PlayStation vs Xbox) and asks if unspecified.
    2. Checks if the inquiry has multiple edition variants (Standard vs Deluxe vs Ultimate) and asks if unspecified.
    3. Seamlessly carries over game franchise context from last_discussed_product across turns.
    Returns: (matched_product, is_ambiguous, candidate_options, reason)
    """
    all_items = (
        catalog.get('subscriptions', []) +
        catalog.get('games', []) +
        catalog.get('hardware_and_accessories', [])
    )

    tokens = [clean_token(w) for w in re.findall(r'[a-zA-Z0-9]+', query.lower())]
    stop_words_effective = set(GENERIC_STOP_WORDS)
    if 'pass' in tokens:
        stop_words_effective.discard('game')
    
    non_stop = [w for w in tokens if w not in stop_words_effective]
    content = [w for w in non_stop if w not in MODIFIER_WORDS and not w.isdigit()]

    user_plat = detect_platform_intent(query)
    has_user_plat = user_plat['ps'] or user_plat['xbox'] or user_plat['pc']

    edition_keywords = ['standard', 'deluxe', 'ultimate', 'definitive', 'gold', 'premium', 'essential', 'extra']
    user_has_edition = any(ed in query.lower() for ed in edition_keywords)

    last_prod = get_last_discussed_product(user_id) if user_id else None

    # Context carry-over: if user didn't mention game name, inherit franchise from last_product
    search_words = list(non_stop)
    effective_content = list(content)
    if len(content) == 0 and last_prod:
        franchise = extract_franchise_from_title(last_prod.get('title', ''))
        f_words = [clean_token(w) for w in re.findall(r'[a-zA-Z0-9]+', franchise.lower())]
        search_words = f_words + search_words
        effective_content = f_words
        # Also inherit edition if user didn't specify one
        if not user_has_edition:
            last_title = last_prod.get('title', '').lower()
            for ed in edition_keywords:
                if ed in last_title:
                    search_words.append(ed)
                    user_has_edition = True
                    break

    scored = []
    for it in all_items:
        s = score_product_match(search_words, it, content_words=effective_content if effective_content else None)
        if s >= 10:
            scored.append((s, it))
    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        return None, False, [], "no_match"

    top_score = scored[0][0]
    top_candidates = [it for s, it in scored if s >= top_score - 18]

    # 1. Platform Ambiguity Check
    if not has_user_plat:
        has_ps = any(detect_item_platform(it)['ps'] for it in top_candidates)
        has_xb = any(detect_item_platform(it)['xbox'] for it in top_candidates)
        if has_ps and has_xb:
            return None, True, top_candidates[:6], "platform_ambiguous"

    # 2. Filter candidates by platform if specified
    if has_user_plat:
        filtered = []
        for it in top_candidates:
            it_plat = detect_item_platform(it)
            if (user_plat['ps'] and it_plat['ps']) or (user_plat['xbox'] and it_plat['xbox']) or (user_plat['pc'] and it_plat['pc']):
                filtered.append(it)
        if filtered:
            top_candidates = filtered

    # 3. Edition Ambiguity Check
    if not user_has_edition and len(top_candidates) > 1:
        editions = set(detect_item_edition(it) for it in top_candidates)
        if len(editions) > 1:
            return None, True, top_candidates[:6], "edition_ambiguous"

    # Resolved to a specific product
    if top_candidates:
        return top_candidates[0], False, [], "resolved"

    return scored[0][1], False, [], "resolved"


def find_best_product_match(query: str, catalog: dict) -> Optional[dict]:
    """Find the single most relevant catalog item matching the user's inquiry."""
    prod, is_ambig, candidates, _ = find_product_or_ambiguity(query, [], catalog)
    if prod:
        return prod
    if candidates:
        return candidates[0]
    return None


def find_best_product_match_with_history(current_query: str, history: list, catalog: dict, user_id: str = "") -> Optional[dict]:
    """Find product with full context carry-over from user session history."""
    prod, is_ambig, candidates, _ = find_product_or_ambiguity(current_query, history, catalog, user_id=user_id)
    if prod:
        return prod
    if candidates:
        return candidates[0]
    return None


