import json
import logging
import re
import time
from typing import Dict, List, Optional
import requests
from config import GEMINI_API_KEY, GEMINI_MODEL, SHOP_NAME, CATALOG_PATH
from order_manager import (
    clean_user_id,
    get_active_order,
    set_active_order,
    is_payment_confirmation,
    is_purchase_intent,
    is_order_placement_request,
    set_presented_options,
    get_presented_options,
    clear_presented_options,
    parse_option_selection,
    find_product_or_ambiguity,
    find_best_product_match,
    find_best_product_match_with_history,
    set_last_discussed_product,
    get_last_discussed_product,
    clean_token,
    GENERIC_STOP_WORDS
)
from woocommerce_client import create_wc_order, update_order_to_processing

logger = logging.getLogger(__name__)

# Fallback models in order of priority & speed
MODEL_CANDIDATES = [
    "gemini-flash-lite-latest",
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash"
]

SESSION_STORAGE: Dict[str, dict] = {}
SESSION_EXPIRY_SECONDS = 7200  # 2 hours session expiry


def load_catalog_data() -> dict:
    """Load latest catalog and store information from catalog.json."""
    try:
        if CATALOG_PATH.exists():
            with open(CATALOG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error loading catalog.json: {e}")
    return {}


def search_relevant_products(query: str, catalog: dict, max_items: int = 18, last_product: Optional[dict] = None) -> list:
    """
    Smart search that finds relevant catalog products for the customer's query.
    Keeps the prompt lightweight and responses ultra-fast (<2s).
    """
    raw_words = re.findall(r'[a-zA-Z0-9]+', query.lower())
    words = [clean_token(w) for w in raw_words if clean_token(w) not in GENERIC_STOP_WORDS]

    all_items = (
        catalog.get("subscriptions", []) +
        catalog.get("games", []) +
        catalog.get("hardware_and_accessories", [])
    )

    scored_items = []
    for item in all_items:
        title = item.get("title", "").lower()
        title_tokens = [clean_token(w) for w in re.findall(r'[a-zA-Z0-9]+', title)]
        title_norm = ' ' + ' '.join(title_tokens) + ' '
        cats = " ".join(item.get("categories", [])).lower()
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

        if score > 0:
            scored_items.append((score, item))

    scored_items.sort(key=lambda x: x[0], reverse=True)
    matched = [item for _, item in scored_items[:max_items]]

    # If a product was recently discussed, ensure it is at the very front of matched items
    if last_product:
        last_id = last_product.get("id")
        matched = [m for m in matched if m.get("id") != last_id]
        full_last_item = next((item for item in all_items if item.get("id") == last_id), last_product)
        matched.insert(0, full_last_item)

    # If few or no matches, provide top popular featured products
    if len(matched) < 6:
        featured_titles = [
            "xbox game pass", "playstation network psn", "ea sports fc 25",
            "tekken", "grand theft auto v", "spider-man", "god of war"
        ]
        for item in all_items:
            title_lower = item.get("title", "").lower()
            if any(f in title_lower for f in featured_titles) and item not in matched:
                matched.append(item)
                if len(matched) >= 12:
                    break

    return matched


def detect_user_language(text: str, current_pref: str = "en") -> str:
    """
    Detect whether the user is writing in English or Roman Urdu.
    Checks for explicit switch requests, Roman Urdu keywords, and English keywords.
    """
    t = text.lower().strip()
    
    # Explicit user requests to change language
    if re.search(r'\b(in english|english please|english plz|speak english|write in english)\b', t):
        return 'en'
    if re.search(r'\b(urdu please|urdu plz|in urdu|urdu me|urdu mein|speak urdu|write in urdu)\b', t):
        return 'ur'
        
    words = set(re.findall(r'[a-zA-Z]+', t))
    
    urdu_markers = {
        'bhai', 'bro', 'kya', 'kia', 'hai', 'hain', 'ha', 'chahiye', 'chaheay',
        'lena', 'leni', 'lene', 'karna', 'karni', 'karne', 'karo', 'kardo', 'krdo',
        'kese', 'kaise', 'kitne', 'kitna', 'kitni', 'batao', 'bata', 'btain',
        'bhejo', 'bhej', 'kar', 'kr', 'dya', 'diya', 'diye', 'wala', 'wali', 'wale',
        'hum', 'ap', 'aap', 'mujhe', 'mujy', 'mjhe', 'pesay', 'paise', 'pese',
        'shukriya', 'shukria', 'theek', 'thik', 'bohat', 'bht', 'acha', 'achha',
        'pehle', 'pehlay', 'zaroor', 'mein', 'par', 'hoga', 'hogi', 'ka', 'ki', 'ke', 'ko', 'se'
    }
    
    english_markers = {
        'i', 'you', 'we', 'they', 'he', 'she', 'it', 'what', 'which', 'who', 'where',
        'when', 'why', 'how', 'is', 'are', 'was', 'were', 'am', 'do', 'does', 'did',
        'have', 'has', 'had', 'want', 'need', 'know', 'rates', 'rate', 'price', 'prices',
        'give', 'send', 'place', 'order', 'please', 'plz', 'for', 'available', 'packages',
        'package', 'can', 'could', 'would', 'should', 'tell', 'show', 'buy', 'account'
    }
    
    u_score = sum(1 for w in words if w in urdu_markers)
    e_score = sum(1 for w in words if w in english_markers)
    
    if u_score > 0 and u_score >= e_score:
        return 'ur'
    elif e_score > 0 and e_score > u_score:
        return 'en'
    elif u_score > 0:
        return 'ur'
    elif e_score > 0:
        return 'en'
    return current_pref


def build_system_instruction(
    catalog: dict,
    user_query: str = "",
    active_order: Optional[dict] = None,
    last_product: Optional[dict] = None,
    user_lang: str = "en",
    is_new_conversation: bool = False,
    ambiguous_info: Optional[dict] = None
) -> str:
    """Generate dynamic system prompt with relevant catalog items, order state, bot profile, and ambiguity rules."""
    shop_info = catalog.get("shop_info", {})
    bot_profile = catalog.get("bot_profile", {})
    bot_name = bot_profile.get("name", "Jack")

    relevant_items = search_relevant_products(user_query, catalog, last_product=last_product)

    items_formatted = []
    for item in relevant_items:
        title = item.get("title", "")
        p_base = item.get("price_pkr", 0)
        p_min = item.get("price_min_pkr", p_base)
        p_max = item.get("price_max_pkr", p_base)

        if p_min and p_max and p_min != p_max:
            price_str = f"PKR {p_min:,} - {p_max:,}"
        else:
            price_str = f"PKR {p_base:,}"

        options = f" | Options: {'; '.join(item.get('options', []))}" if item.get('options') else ""
        stock = " (In Stock)" if item.get("in_stock", True) else " (Out of Stock)"
        link = f" (Buy on {item.get('permalink')})" if item.get('permalink') else ""
        items_formatted.append(f"- {title} -> {price_str}{stock}{options}{link}")

    catalog_context = "\n".join(items_formatted)

    if user_lang == "en":
        if is_new_conversation:
            greeting_rule = 'Start your response with: "Hello! I am Jack from PlayStation World Multan 🎮."'
        else:
            greeting_rule = 'CRITICAL: DO NOT say "Hello! I am Jack from PlayStation World Multan 🎮." or re-introduce yourself! This is an ONGOING conversation. Reply directly and naturally without repeating your name or store intro.'

        lang_directive = f"""
=====================================================
🔴 CRITICAL MANDATORY DIRECTIVE: YOU MUST REPLY 100% IN ENGLISH
The customer is communicating in ENGLISH.
You MUST write your ENTIRE response in clear, polite ENGLISH.
ABSOLUTELY NO Roman Urdu words or phrases (NO "AOA", NO "Main Jack hoon", NO "aap", NO "bhai", NO "shukriya", NO "dein", NO "karein").
{greeting_rule}
Every single sentence, explanation, price quote, and payment account detail MUST be completely in ENGLISH.
=====================================================
"""
    else:
        if is_new_conversation:
            greeting_rule = 'Start your response with: "AOA! Main Jack hoon PlayStation World Multan se 🎮."'
        else:
            greeting_rule = 'CRITICAL: DO NOT repeat "AOA! Main Jack hoon PlayStation World Multan se 🎮." ya apna taaruf mat karwayein! Yeh ONGOING conversation hai. Seedha customer ke sawal ka mukhtasir aur ba-adab jawab dein.'

        lang_directive = f"""
=====================================================
🔴 CRITICAL MANDATORY DIRECTIVE: REPLY IN ROMAN URDU
The customer is communicating in ROMAN URDU.
You MUST write your response in natural, polite Roman Urdu.
{greeting_rule}
=====================================================
"""

    ambiguity_banner = ""
    if ambiguous_info and ambiguous_info.get("is_ambiguous"):
        cands = ambiguous_info.get("candidates", [])
        reason = ambiguous_info.get("reason", "")
        cand_lines = "\n".join([f"- #{c.get('id')} {c.get('title')} (PKR {c.get('price_pkr', 0):,})" for c in cands])
        if user_lang == "en":
            ambiguity_banner = f"""
=====================================================
⚠️ AMBIGUOUS PRODUCT INQUIRY (CONFIRMATION REQUIRED):
Reason: {reason}
The customer asked about a product that is available across multiple platforms or editions.
DO NOT assume the platform (PlayStation vs Xbox) or edition!
DO NOT say an order has been created!
Available options in our store:
{cand_lines}

YOUR TASK:
Politely present the available options with their exact PKR prices and ask the customer to specify which platform (PlayStation or Xbox) or edition they need so we can place their order!
=====================================================
"""
        else:
            ambiguity_banner = f"""
=====================================================
⚠️ AMBIGUOUS PRODUCT INQUIRY (CONFIRMATION REQUIRED):
Reason: {reason}
Customer ne game/product ka pucha hai lekin platform (PlayStation ya Xbox) ya edition specify nahi kiya.
Khud se platform ya edition assume mat karein! Order create mat karein!
Hamare paas dastyab options:
{cand_lines}

YOUR TASK:
Customer ko ba-adab dastyab options aur prices batayein aur unse poochein ke unko in mein se konsa platform (PlayStation ya Xbox) ya edition chahiye taake hum order place kar sakein!
=====================================================
"""

    last_product_banner = ""
    if last_product and not (active_order and active_order.get("status") == "pending"):
        last_product_banner = f"""
=====================================================
RECENTLY DISCUSSED PRODUCT / FRANCHISE:
- Product ID: #{last_product.get('id')}
- Product Title: {last_product.get('title')}
- Official Price: PKR {last_product.get('price_pkr')}

CRITICAL PRODUCT CONTINUITY RULE:
When the customer refers to "this product", "it", "yeh", "order this", "standard edition for xbox", "for xbox", or asks to buy without explicitly naming a new game, they are referring to the product/franchise discussed above: "{last_product.get('title')}".
Always maintain this product context. Never switch or pick an unrelated game like Forza!
=====================================================
"""

    order_banner = ""
    if active_order and active_order.get("status") == "pending":
        if user_lang == "en":
            order_banner = f"""
=====================================================
ACTIVE PENDING ORDER GENERATED FOR THIS CUSTOMER:
- Order Number: #{active_order.get('order_id')}
- Product: {active_order.get('title')}
- Total Amount: PKR {active_order.get('total')}
- Status: Pending Payment

CRITICAL ACTION FOR ORDER #{active_order.get('order_id')} (REPLY IN ENGLISH):
1. Acknowledge and congratulate the customer in ENGLISH that their Order #{active_order.get('order_id')} has been created on playstationworld.pk for "{active_order.get('title')}" (Total: PKR {active_order.get('total')}).
2. Provide our official payment methods clearly in English:
   - 🏦 Bank Transfer: Bank Alfalah | A/C: 09131009641388 | IBAN: PK30ALFH0913001009641388 | Title: PLAYSTATION WORLD MULTAN
   - ⚡ Raast ID: 03054149002 (Title: Junaid Fiaz)
   - 🟢 Easypaisa: 03467146530 (Title: Junaid Fiaz)
   - 🟠 JazzCash / SadaPay / NayaPay: 03054149002 (Title: Junaid Fiaz)
3. Mention the policy in English: "Note: Direct transfers from Easypaisa/Jazzcash to Bank A/C are NOT accepted."
4. Offer QR code in English: "If you prefer scanning a QR code to pay, let us know and we will send the JazzCash or EasyPaisa QR code immediately!"
5. Remind them in English: "Whether you pay via QR code or direct transfer to our bank/wallet, please reply 'paid' or 'done' once completed and send your payment screenshot to WhatsApp 03054149002 so our team can verify and dispatch your order within 5-15 minutes!"
=====================================================
"""
        else:
            order_banner = f"""
=====================================================
ACTIVE PENDING ORDER GENERATED FOR THIS CUSTOMER:
- Order Number: #{active_order.get('order_id')}
- Product: {active_order.get('title')}
- Total Amount: PKR {active_order.get('total')}
- Status: Pending Payment

CRITICAL ACTION FOR ORDER #{active_order.get('order_id')} (REPLY IN ROMAN URDU):
1. Clearly acknowledge and congratulate the customer in Roman Urdu that their Order #{active_order.get('order_id')} has been created on playstationworld.pk for "{active_order.get('title')}" (Total: PKR {active_order.get('total')}).
2. Inform them they can pay using ANY method they prefer (Bank Transfer, Raast, JazzCash, EasyPaisa, or QR code):
   - 🏦 Bank Alfalah: A/C 09131009641388 | IBAN PK30ALFH0913001009641388 | Title: PLAYSTATION WORLD MULTAN
   - ⚡ Raast ID: 03054149002 (Title: Junaid Fiaz)
   - 🟢 Easypaisa: 03467146530 (Title: Junaid Fiaz)
   - 🟠 JazzCash / SadaPay / NayaPay: 03054149002 (Title: Junaid Fiaz)
3. Mention the policy: "Easypaisa/Jazzcash se Direct Bank A/C mein transfer accepted nahi hai."
4. Offer QR code: "Agar aap QR code se scan kar ke pay karna chahte hain toh bata dein, hum JazzCash ya EasyPaisa QR code bhej dete hain!"
5. Remind them: "Chahay aapne QR code se pay kiya ho ya directly Bank/Wallet number par transfer kiya ho, payment ke baad yahan 'paid' ya 'done' likh kar confirm karein aur payment screenshot WhatsApp 03054149002 par bhej dein taake 5-15 minute me unka order verify kar ke deliver ho sake!"
=====================================================
"""

    instruction = f"""
You are "{bot_name}", the official WhatsApp AI Sales & Support Agent for "{SHOP_NAME}" (playstationworld.pk), located in Multan, Pakistan.
Official Website: {shop_info.get('website', 'https://playstationworld.pk')}
Location: {shop_info.get('address', 'Model Town, Multan, Punjab, Pakistan')}
Phone / WhatsApp: {shop_info.get('phone', '03054149002')}
Hours: {shop_info.get('timing', '11:00 AM - 11:30 PM (Digital codes delivery active 24/7)')}

{lang_directive}

OFFICIAL PAYMENT DETAILS:
- 🏦 Bank Transfer: Bank Alfalah | A/C: 09131009641388 | IBAN: PK30ALFH0913001009641388 | Title: PLAYSTATION WORLD MULTAN
- ⚡ Raast ID: 03054149002 (Title: Junaid Fiaz)
- 🟢 Easypaisa: 03467146530 (Title: Junaid Fiaz)
- 🟠 Jazzcash / SadaPay / NayaPay: 03054149002 (Title: Junaid Fiaz)
- ⚠️ POLICY NOTE: Easypaisa/Jazzcash to Bank A/C not Accepted.
- 📱 QR CODE OPTION: JazzCash and EasyPaisa QR codes are available for instant scanning.
- 📸 SCREENSHOT: Customers must send their payment screenshot with Transaction ID to WhatsApp 03054149002 for verification and instant code dispatch (within 5-15 mins).

COMMUNITY & TRUST LINKS (Share when customer asks for reviews, updates, or social links):
- Facebook Page: https://facebook.com/playstationworld.pk
- ✆ Group Updates: https://tinyurl.com/uzu7trec
- ⭐ TrustPilot Reviews: https://www.trustpilot.com/review/playstationworld.pk

{ambiguity_banner}
{last_product_banner}
{order_banner}

CURRENT MATCHING PRODUCTS & OFFICIAL PRICES (PKR):
{catalog_context}

RULES & PERSONA:
1. MANDATORY LANGUAGE MATCHING:
   - If the customer writes in English (or if the directive above says ENGLISH), your response MUST be 100% in English. Never use Roman Urdu words or phrases!
   - If the customer writes in Roman Urdu, your response MUST be in Roman Urdu.
2. Pricing: Quote exact PKR prices from the catalog above.
3. Payment & Order Flow:
   - When a customer is ready to buy or asks for payment details, share the exact payment accounts above cleanly.
   - Mention the policy: "Easypaisa/Jazzcash to Bank A/C not Accepted."
   - Remind them to confirm once paid and send the payment screenshot to WhatsApp 03054149002 for immediate code dispatch.
4. Trust & Reviews: If customer wants proof, community updates, or reviews, share our TrustPilot (https://www.trustpilot.com/review/playstationworld.pk), WhatsApp Group (https://tinyurl.com/uzu7trec), or Facebook.
5. Product Context Continuity: When a customer asks to order or buy 'this product', 'it', 'yeh', 'yehi', or refers to what was discussed in the immediately preceding response, ALWAYS stick strictly to that exact product. Never invent or switch to a different product.
6. Ambiguity & Platform Confirmation: If a game or product is available for multiple platforms (e.g. PlayStation vs Xbox) or editions, and the customer has not specified which one, ALWAYS present the choices and ask them to confirm before placing an order.
7. Order Placement on Website: When a customer selects an option, ask: "Would you like me to place this order on our website for you?" When they say "Yes" or ask you to place it, NEVER tell them to visit the site or add to cart themselves! The order is automatically created by the system on playstationworld.pk, and you share their Order # and payment methods right here.
8. Natural Chat Flow: DO NOT repeat introductory greetings ("Hello! I am Jack from PlayStation World Multan") on subsequent messages after the initial greeting turn. Speak naturally like a real human in a conversation.
9. Keep replies well-spaced, courteous, and easy to read on mobile WhatsApp.
"""
    return instruction.strip()


def get_or_create_session_data(user_id: str) -> dict:
    """Retrieve or create session dict with history and language preferences."""
    cid = clean_user_id(user_id)
    now = time.time()
    if cid in SESSION_STORAGE:
        session = SESSION_STORAGE[cid]
        if now - session.get("timestamp", 0) < SESSION_EXPIRY_SECONDS:
            session["timestamp"] = now
            return session

    SESSION_STORAGE[cid] = {
        "timestamp": now,
        "history": [],
        "language": "en"
    }
    return SESSION_STORAGE[cid]


def get_or_create_session(user_id: str) -> list:
    """Retrieve or create conversation history for a given customer WhatsApp number."""
    return get_or_create_session_data(user_id)["history"]


def clear_session(user_id: str):
    """Reset session for a customer."""
    cid = clean_user_id(user_id)
    if cid in SESSION_STORAGE:
        del SESSION_STORAGE[cid]


def call_gemini_api(system_prompt: str, history: list, new_user_message: str) -> str:
    """Call Google Gemini REST API with automatic multi-model failover."""
    if not GEMINI_API_KEY:
        return (
            "⚠️ [PlayStation World Multan Bot]: GEMINI_API_KEY is not configured yet. "
            "Please add your Gemini API key in the dashboard or .env file to enable AI replies."
        )

    # Format contents for Gemini API
    contents = []
    for turn in history[-6:]:
        contents.append({
            "role": turn["role"],
            "parts": [{"text": turn["text"]}]
        })

    contents.append({
        "role": "user",
        "parts": [{"text": new_user_message}]
    })

    payload = {
        "contents": contents,
        "systemInstruction": {
            "parts": [{"text": system_prompt}]
        },
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 800,
            "topP": 0.95
        }
    }

    models_to_try = [GEMINI_MODEL.strip().lower()] if GEMINI_MODEL else []
    for m in MODEL_CANDIDATES:
        if m not in models_to_try:
            models_to_try.append(m)

    last_error = None
    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
        try:
            logger.info(f"Invoking Gemini model: {model_name}...")
            response = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=18
            )

            if response.status_code == 200:
                data = response.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [{}])
                    for part in parts:
                        text = part.get("text", "")
                        if text:
                            return text.strip()
                return "Walaikum Assalam! PlayStation World Multan me khushamdeed! Main aapki kya madad kar sakta hoon? 🎮"
            else:
                logger.warning(f"Model {model_name} returned status {response.status_code}: {response.text[:150]}")
                last_error = f"Status {response.status_code}"
                continue

        except requests.exceptions.Timeout:
            logger.warning(f"Timeout on model {model_name}, trying fallback...")
            last_error = "Timeout"
            continue
        except Exception as e:
            logger.warning(f"Exception on model {model_name}: {e}")
            last_error = str(e)
            continue

    logger.error(f"All Gemini models exhausted. Last error: {last_error}")
    return (
        "AOA! Welcome to PlayStation World Multan! 🎮\n\n"
        "Abhi network traffic ziada hone ki wajah se reply me delay aya hai. "
        "Aap hamare direct number par call ya WhatsApp message kar sakte hain: *0305-4149002* (Model Town, Multan) "
        "ya hamari website visit karein: *https://playstationworld.pk* ✨"
    )


def handle_incoming_message(user_id: str, message_text: str, customer_name: str = "Customer") -> str:
    """
    Main entry point for handling customer messages.
    Integrates order creation, payment status transitions, language matching, and AI responses.
    """
    catalog = load_catalog_data()
    session_data = get_or_create_session_data(user_id)
    prev_lang = session_data.get("language", "en")
    user_lang = detect_user_language(message_text, current_pref=prev_lang)
    session_data["language"] = user_lang
    history = session_data["history"]

    # -------------------------------------------------------------
    # 1. PAYMENT CONFIRMATION FLOW ("paid", "done", "bayan kar diya", etc.)
    # -------------------------------------------------------------
    if is_payment_confirmation(message_text):
        active_order = get_active_order(user_id)
        if active_order and active_order.get("status") == "pending":
            order_id = active_order.get("order_id")
            title = active_order.get("title", "Game / Subscription")

            # Update WooCommerce order to processing
            update_order_to_processing(order_id)
            set_active_order(
                user_id=user_id,
                order_id=order_id,
                product_id=active_order.get("product_id"),
                title=title,
                total=active_order.get("total", "0"),
                status="processing"
            )

            if user_lang == "en":
                reply = (
                    f"✅ **Thank you!**\n\n"
                    f"Your Order **#{order_id}** ({title}) has been marked as **'Processing'** on our website (*playstationworld.pk*)! 🎮\n\n"
                    f"📸 **Next Step (Payment Verification):**\n"
                    f"Whether you paid via **Bank Transfer, Raast, JazzCash, EasyPaisa, or QR Code**, please forward your **Payment Screenshot** and **Transaction ID** to our official WhatsApp:\n"
                    f"👉 **WhatsApp: 03054149002**\n\n"
                    f"Once verified, our team will dispatch your digital code / account details within 5-15 minutes! Thank you! ✨"
                )
            else:
                reply = (
                    f"✅ **Bohat Shukriya / Thank you!**\n\n"
                    f"Aapka Order **#{order_id}** ({title}) hamari website (*playstationworld.pk*) par **'Processing'** mark ho chuka hai! 🎮\n\n"
                    f"📸 **Agla Qadam (Payment Verification):**\n"
                    f"Chahay aapne **Bank Transfer, Raast, JazzCash, EasyPaisa, ya QR Code** ke zariye payment ki ho, baraye meherbani apni payment ka **Screenshot** aur **Transaction ID** foran hamare official WhatsApp par bhej dein:\n"
                    f"👉 **WhatsApp: 03054149002**\n\n"
                    f"Hamari team payment verify hotay hi (5-15 minute ke andar) aapka digital code / account details deliver kar degi! Shukriya! ✨"
                )
            history.append({"role": "user", "text": message_text})
            history.append({"role": "model", "text": reply})
            return reply

        elif active_order and active_order.get("status") == "processing":
            order_id = active_order.get("order_id")
            if user_lang == "en":
                reply = (
                    f"Hello! Your Order **#{order_id}** is already in processing. 🎮\n\n"
                    f"📸 Whether you scanned a QR code or transferred to our Bank/Wallet, if you have not forwarded your payment screenshot yet, please WhatsApp it to **03054149002** so our team can verify and send your details within 5-15 minutes!"
                )
            else:
                reply = (
                    f"AOA! Aapka Order **#{order_id}** pehle se hi processing mein hai. 🎮\n\n"
                    f"📸 Chahay aapne QR code scan kiya ho ya Bank/Wallet number par transfer kiya ho, agar aapne payment ka screenshot abhi tak nahi bheja toh foran hamare WhatsApp **03054149002** par bhej dein taake 5-15 minute me verify ho kar code mil jaye!"
                )
            history.append({"role": "user", "text": message_text})
            history.append({"role": "model", "text": reply})
            return reply

        else:
            # Check if customer mentioned or previously inquired about a product, so we can generate the order right now!
            matched_item = find_best_product_match_with_history(message_text, history, catalog, user_id=user_id)
            if matched_item:
                prod_id = matched_item.get("id")
                prod_title = matched_item.get("title")
                price = matched_item.get("price_pkr", 0)

                order_id, total = create_wc_order(
                    customer_phone=user_id,
                    customer_name=customer_name,
                    product_id=prod_id,
                    quantity=1,
                    product_title=prod_title,
                    price=price
                )
                if order_id:
                    update_order_to_processing(order_id)
                    set_active_order(
                        user_id=user_id,
                        order_id=order_id,
                        product_id=prod_id,
                        title=prod_title,
                        total=total or str(price),
                        status="processing"
                    )
                    if user_lang == "en":
                        reply = (
                            f"✅ **Thank you!**\n\n"
                            f"Your Order **#{order_id}** ({prod_title}) has been created and marked as **'Processing'** on our website (*playstationworld.pk*)! 🎮\n\n"
                            f"📸 **Next Step (Payment Verification):**\n"
                            f"Whether you paid via **Bank Transfer, Raast, JazzCash, EasyPaisa, or QR Code**, please send your **Payment Screenshot** and **Transaction ID** to our official WhatsApp:\n"
                            f"👉 **WhatsApp: 03054149002**\n\n"
                            f"Once verified, our team will dispatch your code/details within 5-15 minutes! Thank you! ✨"
                        )
                    else:
                        reply = (
                            f"✅ **Bohat Shukriya / Thank you!**\n\n"
                            f"Aapka Order **#{order_id}** ({prod_title}) create kar ke hamari website (*playstationworld.pk*) par **'Processing'** mark kar diya gaya hai! 🎮\n\n"
                            f"📸 **Agla Qadam (Payment Verification):**\n"
                            f"Chahay aapne **Bank Transfer, Raast, JazzCash, EasyPaisa, ya QR Code** ke zariye payment ki ho, baraye meherbani apni payment ka **Screenshot** aur **Transaction ID** foran hamare official WhatsApp par bhej dein:\n"
                            f"👉 **WhatsApp: 03054149002**\n\n"
                            f"Hamari team payment verify hotay hi (5-15 minute ke andar) aapka digital code / account details deliver kar degi! Shukriya! ✨"
                        )
                    history.append({"role": "user", "text": message_text})
                    history.append({"role": "model", "text": reply})
                    return reply

            if user_lang == "en":
                reply = (
                    "Hello! Welcome to PlayStation World Multan! 🎮\n\n"
                    "Whether you paid via **Bank Transfer, Raast, JazzCash, EasyPaisa, or QR Code** — please let us know which game or subscription code you need, and send your **Payment Screenshot** and **Transaction ID** to our official WhatsApp:\n"
                    "👉 **WhatsApp: 03054149002**\n\n"
                    "We will create and verify your order on our website and deliver within 5-15 minutes! ✨"
                )
            else:
                reply = (
                    "AOA! PlayStation World Multan me khushamdeed! 🎮\n\n"
                    "Chahay aapne **Bank Transfer, Raast, JazzCash, EasyPaisa, ya QR Code** kisi bhi tareeqay se payment ki ho — baraye meherbani batayein aapko konsi game ya subscription chahiye, aur apni payment ka **Screenshot** aur **Transaction ID** foran hamare official WhatsApp par bhej dein:\n"
                    "👉 **WhatsApp: 03054149002**\n\n"
                    "Hum aapka order website par create aur verify kar ke 5-15 minute ke andar deliver kar denge! ✨"
                )
            history.append({"role": "user", "text": message_text})
            history.append({"role": "model", "text": reply})
            return reply

    # -------------------------------------------------------------
    # 2. OPTION SELECTION & WEBSITE ORDER CONFIRMATION FLOW
    # -------------------------------------------------------------
    active_order = get_active_order(user_id)
    last_discussed = get_last_discussed_product(user_id)

    # A. Check if customer is selecting an option (e.g. "option 1", "option 2", "1", "2")
    presented_options = get_presented_options(user_id)
    selected_option = parse_option_selection(message_text, presented_options)

    if selected_option:
        set_last_discussed_product(user_id, selected_option)
        clear_presented_options(user_id)
        prod_title = selected_option.get("title", "Game")
        prod_price = selected_option.get("price_pkr", 0)

        if user_lang == "en":
            reply = (
                f"Excellent choice! You selected **{prod_title}** for **PKR {prod_price:,}**.\n\n"
                f"Would you like me to place this order on our website (*playstationworld.pk*) for you? Reply **\"Yes\"** to confirm!"
            )
        else:
            reply = (
                f"Zabardast choice! Aapne **{prod_title}** select kiya hai (Price: **PKR {prod_price:,}**).\n\n"
                f"Kya main aapka yeh order hamari website (*playstationworld.pk*) par place kar doon? Confirm karne ke liye **\"Haan\"** ya **\"Yes\"** likhein!"
            )
        history.append({"role": "user", "text": message_text})
        history.append({"role": "model", "text": reply})
        return reply

    # B. Check if customer says "yes" or asks bot to place order on website
    if is_order_placement_request(message_text) and last_discussed:
        prod_id = last_discussed.get("id")
        prod_title = last_discussed.get("title")
        price = last_discussed.get("price_pkr", 0)

        if not active_order or active_order.get("product_id") != prod_id or active_order.get("status") != "pending":
            order_id, total = create_wc_order(
                customer_phone=user_id,
                customer_name=customer_name,
                product_id=prod_id,
                quantity=1,
                product_title=prod_title,
                price=price
            )
            if order_id:
                set_active_order(
                    user_id=user_id,
                    order_id=order_id,
                    product_id=prod_id,
                    title=prod_title,
                    total=total or str(price),
                    status="pending"
                )
                active_order = get_active_order(user_id)
            else:
                if user_lang == "en":
                    reply = (
                        f"⚠️ I attempted to place your order for **{prod_title}** on our website (*playstationworld.pk*), "
                        f"but our store API is currently connecting or updating. Please message us directly on WhatsApp at **03054149002** "
                        f"so we can confirm and process your order right away!"
                    )
                else:
                    reply = (
                        f"⚠️ Humne aapka order **{prod_title}** website (*playstationworld.pk*) par place karne ki koshish ki "
                        f"lekin website API connection me masla aya hai. Baraye meherbani foran hamare WhatsApp **03054149002** "
                        f"par rabta karein taake hum aapka order foran process kar sakein!"
                    )
                history.append({"role": "user", "text": message_text})
                history.append({"role": "model", "text": reply})
                return reply

    # C. Product resolution & ambiguity check across all catalog items
    matched_item, is_ambiguous, candidates, ambig_reason = find_product_or_ambiguity(
        message_text, history, catalog, user_id=user_id
    )

    ambiguous_info = None
    if is_ambiguous and candidates:
        ambiguous_info = {
            "is_ambiguous": True,
            "candidates": candidates,
            "reason": ambig_reason
        }
        # Save options so user can reply "option 1", "1", etc.
        set_presented_options(user_id, candidates)
        first_cand = candidates[0]
        set_last_discussed_product(user_id, first_cand)

    elif matched_item:
        set_last_discussed_product(user_id, matched_item)

        # Direct purchase intent with fully disambiguated product
        if is_purchase_intent(message_text) or is_order_placement_request(message_text):
            prod_id = matched_item.get("id")
            prod_title = matched_item.get("title")
            price = matched_item.get("price_pkr", 0)

            if not active_order or active_order.get("product_id") != prod_id or active_order.get("status") != "pending":
                order_id, total = create_wc_order(
                    customer_phone=user_id,
                    customer_name=customer_name,
                    product_id=prod_id,
                    quantity=1,
                    product_title=prod_title,
                    price=price
                )
                if order_id:
                    set_active_order(
                        user_id=user_id,
                        order_id=order_id,
                        product_id=prod_id,
                        title=prod_title,
                        total=total or str(price),
                        status="pending"
                    )
                    active_order = get_active_order(user_id)
                else:
                    if user_lang == "en":
                        reply = (
                            f"⚠️ I attempted to place your order for **{prod_title}** on our website (*playstationworld.pk*), "
                            f"but our store API is currently connecting or updating. Please message us directly on WhatsApp at **03054149002** "
                            f"so we can confirm and process your order right away!"
                        )
                    else:
                        reply = (
                            f"⚠️ Humne aapka order **{prod_title}** website (*playstationworld.pk*) par place karne ki koshish ki "
                            f"lekin website API connection me masla aya hai. Baraye meherbani foran hamare WhatsApp **03054149002** "
                            f"par rabta karein taake hum aapka order foran process kar sakein!"
                        )
                    history.append({"role": "user", "text": message_text})
                    history.append({"role": "model", "text": reply})
                    return reply

    # -------------------------------------------------------------
    # 3. AI RESPONSE GENERATION
    # -------------------------------------------------------------
    is_new_conv = (len(history) == 0)
    last_discussed = get_last_discussed_product(user_id)
    system_prompt = build_system_instruction(
        catalog,
        user_query=message_text,
        active_order=active_order,
        last_product=last_discussed,
        user_lang=user_lang,
        is_new_conversation=is_new_conv,
        ambiguous_info=ambiguous_info
    )
    ai_reply = call_gemini_api(system_prompt, history, message_text)

    # Update conversation history
    history.append({"role": "user", "text": message_text})
    history.append({"role": "model", "text": ai_reply})

    return ai_reply
