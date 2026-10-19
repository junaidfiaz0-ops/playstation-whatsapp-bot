import json
import logging
import re
import time
from typing import Dict, List, Optional
import requests
from config import GEMINI_API_KEY, GEMINI_MODEL, SHOP_NAME, CATALOG_PATH
from order_manager import (
    get_active_order,
    set_active_order,
    is_payment_confirmation,
    is_purchase_intent,
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


def build_system_instruction(catalog: dict, user_query: str = "", active_order: Optional[dict] = None, last_product: Optional[dict] = None) -> str:
    """Generate dynamic system prompt with relevant catalog items, order state, and bot profile."""
    shop_info = catalog.get("shop_info", {})
    bot_profile = catalog.get("bot_profile", {})
    bot_name = bot_profile.get("name", "Jack")
    welcome_en = bot_profile.get("welcome_message", f"Hello! I am {bot_name} bot from PlayStation World Multan 🎮. Ask your questions!")
    welcome_ur = bot_profile.get("welcome_message_urdu", f"AOA! Main {bot_name} hoon PlayStation World Multan se 🎮. Apne sawalat poochein, main aapki poori madad karunga!")

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

    last_product_banner = ""
    if last_product and not (active_order and active_order.get("status") == "pending"):
        last_product_banner = f"""
=====================================================
RECENTLY DISCUSSED PRODUCT:
- Product ID: #{last_product.get('id')}
- Product Title: {last_product.get('title')}
- Official Price: PKR {last_product.get('price_pkr')}

CRITICAL PRODUCT CONTINUITY RULE:
When the customer refers to "this product", "it", "yeh", "order this", "place order", or asks to buy without explicitly naming a new game, they are referring to the product discussed above: "{last_product.get('title')}".
Always maintain this product context. Never switch or pick an unrelated game!
=====================================================
"""

    order_banner = ""
    if active_order and active_order.get("status") == "pending":
        order_banner = f"""
=====================================================
ACTIVE PENDING ORDER GENERATED FOR THIS CUSTOMER:
- Order Number: #{active_order.get('order_id')}
- Product: {active_order.get('title')}
- Total Amount: PKR {active_order.get('total')}
- Status: Pending Payment

CRITICAL ACTION FOR ORDER #{active_order.get('order_id')}:
1. Clearly acknowledge and congratulate the customer that their Order #{active_order.get('order_id')} has been created on playstationworld.pk for "{active_order.get('title')}" (Total: PKR {active_order.get('total')}).
2. Inform them they can pay using ANY method they prefer (Bank Transfer, Raast, JazzCash, EasyPaisa, or QR code):
   - 🏦 Bank Alfalah: A/C 09131009641388 | IBAN PK30ALFH0913001009641388 | Title: PLAYSTATION WORLD MULTAN
   - ⚡ Raast ID: 03054149002 (Title: Junaid Fiaz)
   - 🟢 Easypaisa: 03467146530 (Title: Junaid Fiaz)
   - 🟠 JazzCash / SadaPay / NayaPay: 03054149002 (Title: Junaid Fiaz)
3. Mention the policy: "Easypaisa/Jazzcash to Bank A/C not Accepted."
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

{last_product_banner}
{order_banner}

CURRENT MATCHING PRODUCTS & OFFICIAL PRICES (PKR):
{catalog_context}

RULES & PERSONA:
1. Greet warmly and introduce yourself as {bot_name}:
   - In English: "{welcome_en}"
   - In Roman Urdu: "{welcome_ur}"
2. Match customer's language: If customer speaks in Roman Urdu ("Bhai rate kya hai?"), respond in natural, friendly Roman Urdu. If English, reply in English.
3. Pricing: Quote exact PKR prices from the catalog above.
4. Payment & Order Flow:
   - When a customer is ready to buy or asks for payment details, share the exact payment accounts above cleanly.
   - Mention the policy: "Easypaisa/Jazzcash to Bank A/C not Accepted."
   - Ask them: "Agar aap QR code se payment karna chahte hain toh bata dein, hum JazzCash ya EasyPaisa QR code bhej dete hain!"
   - Remind them to confirm once paid and send the payment screenshot to WhatsApp 03054149002 for immediate code dispatch.
5. Trust & Reviews: If customer wants proof, community updates, or reviews, share our TrustPilot (https://www.trustpilot.com/review/playstationworld.pk), WhatsApp Group (https://tinyurl.com/uzu7trec), or Facebook.
6. Product Context Continuity: When a customer asks to order or buy 'this product', 'it', 'yeh', 'yehi', or refers to what was discussed in the immediately preceding response, ALWAYS stick strictly to that exact product. Never invent or switch to a different product.
7. Keep replies well-spaced, courteous, and easy to read on mobile WhatsApp.
"""
    return instruction.strip()


def get_or_create_session(user_id: str) -> list:
    """Retrieve or create conversation history for a given customer WhatsApp number."""
    now = time.time()
    if user_id in SESSION_STORAGE:
        session = SESSION_STORAGE[user_id]
        if now - session.get("timestamp", 0) < SESSION_EXPIRY_SECONDS:
            session["timestamp"] = now
            return session["history"]

    SESSION_STORAGE[user_id] = {
        "timestamp": now,
        "history": []
    }
    return SESSION_STORAGE[user_id]["history"]


def clear_session(user_id: str):
    """Reset session for a customer."""
    if user_id in SESSION_STORAGE:
        del SESSION_STORAGE[user_id]


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
    Integrates order creation, payment status transitions, and AI responses.
    """
    catalog = load_catalog_data()
    history = get_or_create_session(user_id)

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
            reply = (
                f"AOA! Aapka Order **#{order_id}** pehle se hi processing mein hai. 🎮\n\n"
                f"📸 Chahay aapne QR code scan kiya ho ya Bank/Wallet number par transfer kiya ho, agar aapne payment ka screenshot abhi tak nahi bheja toh foran hamare WhatsApp **03054149002** par bhej dein taake 5-15 minute me verify ho kar code mil jaye!"
            )
            history.append({"role": "user", "text": message_text})
            history.append({"role": "model", "text": reply})
            return reply

        else:
            # Check if customer mentioned or previously inquired about a product, so we can generate the order right now!
            matched_item = find_best_product_match_with_history(message_text, history, catalog)
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
    # 2. PRODUCT CONTEXT TRACKING & ORDER CREATION FLOW
    # -------------------------------------------------------------
    active_order = get_active_order(user_id)

    # 1. Track any explicitly mentioned product in the customer's query
    explicit_match = find_best_product_match(message_text, catalog)
    if explicit_match:
        set_last_discussed_product(user_id, explicit_match)

    # 2. Check purchase intent (e.g. "place order for this product", "buy it", "yeh chahiye")
    if is_purchase_intent(message_text):
        matched_item = find_best_product_match_with_history(message_text, history, catalog, user_id=user_id)
        if matched_item:
            set_last_discussed_product(user_id, matched_item)
            prod_id = matched_item.get("id")
            prod_title = matched_item.get("title")
            price = matched_item.get("price_pkr", 0)

            # If no active order or different product or previous order completed
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

    # -------------------------------------------------------------
    # 3. AI RESPONSE GENERATION
    # -------------------------------------------------------------
    last_discussed = get_last_discussed_product(user_id)
    system_prompt = build_system_instruction(catalog, user_query=message_text, active_order=active_order, last_product=last_discussed)
    ai_reply = call_gemini_api(system_prompt, history, message_text)

    # Update conversation history
    history.append({"role": "user", "text": message_text})
    history.append({"role": "model", "text": ai_reply})

    return ai_reply
