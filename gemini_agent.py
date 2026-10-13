import json
import logging
import re
import time
from typing import Dict, List, Optional
import requests
from config import GEMINI_API_KEY, GEMINI_MODEL, SHOP_NAME, CATALOG_PATH

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


def search_relevant_products(query: str, catalog: dict, max_items: int = 18) -> list:
    """
    Smart search that finds relevant catalog products for the customer's query.
    Keeps the prompt lightweight and responses ultra-fast (<2s).
    """
    query_lower = query.lower()
    # Extract keywords
    words = set(re.findall(r'[a-zA-Z0-9]+', query_lower))

    all_items = (
        catalog.get("subscriptions", []) +
        catalog.get("games", []) +
        catalog.get("hardware_and_accessories", [])
    )

    scored_items = []
    for item in all_items:
        title = item.get("title", "").lower()
        cats = " ".join(item.get("categories", [])).lower()
        score = 0

        # Exact title match
        if title in query_lower:
            score += 10

        # Keyword matching
        for word in words:
            if len(word) > 2:
                if word in title:
                    score += 4
                elif word in cats:
                    score += 2

        if score > 0:
            scored_items.append((score, item))

    scored_items.sort(key=lambda x: x[0], reverse=True)
    matched = [item for _, item in scored_items[:max_items]]

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


def build_system_instruction(catalog: dict, user_query: str = "") -> str:
    """Generate dynamic system prompt with relevant catalog items."""
    shop_info = catalog.get("shop_info", {})
    relevant_items = search_relevant_products(user_query, catalog)

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

    instruction = f"""
You are the official WhatsApp AI Sales & Support Agent for "{SHOP_NAME}", the premier gaming store in Multan, Pakistan.
Official Website: {shop_info.get('website', 'https://playstationworld.pk')}
Location: {shop_info.get('address', 'Model Town, Multan, Punjab, Pakistan')}
Phone / WhatsApp: {shop_info.get('phone', '03054149002')}
Hours: 11:00 AM - 11:30 PM (Digital codes delivery active 24/7)
Payment Methods: JazzCash (0305-4149002), EasyPaisa (0305-4149002), Raast, Bank Transfer, Debit/Credit Card online on playstationworld.pk, Cash pickup in Model Town Multan.
Delivery: Digital codes sent on WhatsApp within 5-15 mins of payment screenshot. Physical items shipped via TCS/Leopard or bike rider in Multan.

CURRENT MATCHING PRODUCTS & OFFICIAL PRICES (PKR):
{catalog_context}

RULES & PERSONA:
1. Greet warmly: "AOA! Welcome to PlayStation World Multan! 🎮"
2. Match customer's language: If customer speaks in Roman Urdu ("Bhai rate kya hai?"), respond in natural, friendly Roman Urdu ("Walaikum Assalam bhai!"). If English, reply in English.
3. Pricing: Quote exact PKR prices from the catalog above. If an item edition or variation is needed, ask politely (e.g. PS5 vs PS4, 1 month vs 12 months, digital vs disc).
4. Ordering: When customer wants to buy, provide order summary, payment accounts (JazzCash / EasyPaisa: 0305-4149002), and request payment screenshot for instant code dispatch.
5. Location: If asked, tell them we are located at Model Town, Multan, and they can visit our shop or order online.
6. Keep replies concise, helpful, and formatted for easy WhatsApp reading.
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

    # Prepare model list: preferred model first, then fallbacks
    models_to_try = [GEMINI_MODEL] if GEMINI_MODEL else []
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
                timeout=18  # 18s timeout per model
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


def handle_incoming_message(user_id: str, message_text: str) -> str:
    """
    Main entry point for handling customer messages.
    """
    catalog = load_catalog_data()
    system_prompt = build_system_instruction(catalog, user_query=message_text)
    history = get_or_create_session(user_id)

    ai_reply = call_gemini_api(system_prompt, history, message_text)

    # Update conversation history
    history.append({"role": "user", "text": message_text})
    history.append({"role": "model", "text": ai_reply})

    return ai_reply
