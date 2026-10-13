import json
import logging
import re
import requests
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CATALOG_PATH = BASE_DIR / "catalog.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def clean_html(raw_html: str) -> str:
    """Strip HTML tags and unescape entities."""
    if not raw_html:
        return ""
    clean = re.sub(r'<[^>]+>', '', raw_html)
    clean = clean.replace('&#8211;', '-').replace('&amp;', '&').replace('&#038;', '&').replace('&quot;', '"').strip()
    return clean

def parse_price(price_str: str, minor_unit: int = 2) -> int:
    """Convert minor unit price string (e.g. '299900') to integer PKR (2999)."""
    try:
        val = int(price_str)
        return int(val / (10 ** minor_unit))
    except Exception:
        return 0

def fetch_all_products():
    """Fetch all products from playstationworld.pk via WooCommerce Store API."""
    all_products = []
    page = 1
    per_page = 100

    while True:
        url = f"https://playstationworld.pk/wp-json/wc/store/v1/products?per_page={per_page}&page={page}"
        print(f"Fetching page {page} from playstationworld.pk...")
        try:
            res = requests.get(url, headers=HEADERS, timeout=25)
            if res.status_code != 200:
                print(f"Finished fetching at page {page} (Status: {res.status_code})")
                break
            items = res.json()
            if not items or not isinstance(items, list):
                break
            all_products.extend(items)
            print(f"Fetched {len(items)} items on page {page}. Total: {len(all_products)}")
            if len(items) < per_page:
                break
            page += 1
        except Exception as e:
            print(f"Error fetching page {page}: {e}")
            break

    return all_products

def fetch_product_variations(product_id: int):
    """Fetch variation options for variable products (like subscriptions or editions)."""
    # Try fetching variations endpoint or product details
    url = f"https://playstationworld.pk/wp-json/wc/store/v1/products/{product_id}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        if res.status_code == 200:
            return res.json().get("variations", [])
    except Exception:
        pass
    return []

def main():
    print("=== STARTING CATALOG SYNC FROM PLAYSTATIONWORLD.PK ===")
    products = fetch_all_products()
    print(f"Total raw products fetched: {len(products)}")

    # Store Information from playstationworld.pk official details
    shop_info = {
        "name": "PlayStation World Multan",
        "website": "https://playstationworld.pk",
        "city": "Multan, Pakistan",
        "address": "Model Town, Multan, Punjab, Pakistan (Postal Code: 60600)",
        "phone": "03054149002",
        "whatsapp": "+92 305 4149002",
        "timing": "Monday to Sunday: Open 24/7 online orders | Store support: 11:00 AM - 11:30 PM",
        "social_media": {
            "facebook": "https://www.facebook.com/playstationworld.pk",
            "instagram": "https://www.instagram.com/playstationworld.pk",
            "twitter_x": "https://x.com/Playstationworld"
        },
        "payment_methods": {
            "JazzCash": "0305-4149002 (Title: PlayStation World / Junaid)",
            "EasyPaisa": "0305-4149002 (Title: PlayStation World / Junaid)",
            "Raast_Transfers": "Available to 0305-4149002",
            "Bank_Transfer": "Meezan Bank / Bank Alfalah / Online Bank Transfer (IBAN provided on checkout)",
            "Debit_Credit_Cards": "Accepted online on playstationworld.pk",
            "Cash_On_Pickup": "Available at Model Town, Multan store"
        },
        "delivery": {
            "digital_codes": "Instant delivery via WhatsApp & Email (within 5-15 mins after payment verification)",
            "physical_items": "Same-day rider delivery in Multan; TCS/Leopard Courier across Pakistan"
        }
    }

    subscriptions = []
    games = []
    hardware_and_accessories = []
    other_items = []

    for p in products:
        p_id = p.get("id")
        raw_name = p.get("name", "")
        name = clean_html(raw_name)
        categories = [clean_html(c.get("name", "")) for c in p.get("categories", [])]
        cat_str = " ".join(categories).lower()
        desc = clean_html(p.get("description", ""))
        short_desc = clean_html(p.get("short_description", ""))
        full_desc = short_desc if short_desc else (desc[:200] if desc else "")
        is_in_stock = p.get("is_in_stock", True)
        
        prices_obj = p.get("prices", {})
        minor_unit = prices_obj.get("currency_minor_unit", 2)
        base_price = parse_price(prices_obj.get("price", "0"), minor_unit)
        regular_price = parse_price(prices_obj.get("regular_price", "0"), minor_unit)

        price_range = prices_obj.get("price_range")
        min_price = parse_price(price_range.get("min_amount", "0"), minor_unit) if price_range else base_price
        max_price = parse_price(price_range.get("max_amount", "0"), minor_unit) if price_range else base_price

        # Check for attributes (e.g. durations, regions, editions)
        attributes = p.get("attributes", [])
        attr_summary = []
        for attr in attributes:
            attr_name = attr.get("name", "")
            terms = [t.get("name") for t in attr.get("terms", [])]
            if terms:
                attr_summary.append(f"{attr_name}: {', '.join(terms)}")

        item_data = {
            "id": p_id,
            "title": name,
            "categories": categories,
            "price_pkr": base_price,
            "regular_price_pkr": regular_price,
            "price_min_pkr": min_price,
            "price_max_pkr": max_price,
            "in_stock": is_in_stock,
            "description": full_desc,
            "options": attr_summary,
            "permalink": p.get("permalink", "")
        }

        # Categorize
        name_lower = name.lower()
        if any(keyword in name_lower or keyword in cat_str for keyword in [
            "game pass", "ps plus", "playstation plus", "psn card", "gift card", "wallet", 
            "subscription", "ea play", "nintendo eshop", "steam wallet"
        ]):
            subscriptions.append(item_data)
        elif any(keyword in name_lower or keyword in cat_str for keyword in [
            "console", "controller", "dualsense", "headset", "charging", "accessory", "jailbreak"
        ]):
            hardware_and_accessories.append(item_data)
        elif any(keyword in name_lower or keyword in cat_str for keyword in [
            "game", "edition", "ps4", "ps5", "xbox", "digital", "disc", "remastered"
        ]):
            games.append(item_data)
        else:
            other_items.append(item_data)

    new_catalog = {
        "shop_info": shop_info,
        "subscriptions": subscriptions,
        "games": games,
        "hardware_and_accessories": hardware_and_accessories,
        "other_products": other_items,
        "frequently_asked_questions": [
            {
                "question": "How do I receive digital codes and accounts?",
                "answer": "Digital subscription codes and account logins are sent directly on WhatsApp or Email within 5 to 15 minutes after you send your payment screenshot."
            },
            {
                "question": "What payment methods does PlayStation World Multan accept?",
                "answer": "We accept JazzCash (0305-4149002), EasyPaisa (0305-4149002), Raast, Bank Transfer (Meezan/Alfalah), Debit/Credit Cards on playstationworld.pk, and Cash on Pickup at our Model Town Multan store."
            },
            {
                "question": "Where is PlayStation World located in Multan?",
                "answer": "We are located at Model Town, Multan, Punjab, Pakistan. Customers in Multan can visit us in person or request same-day bike rider delivery."
            },
            {
                "question": "Do you deliver physical discs and consoles outside Multan?",
                "answer": "Yes! We ship all over Pakistan via TCS and Leopard Courier with secure bubble packaging."
            },
            {
                "question": "Are games and subscription codes genuine?",
                "answer": "100% genuine retail keys and subscriptions with warranty and instant customer support."
            }
        ]
    }

    with open(CATALOG_PATH, "w", encoding="utf-8") as f:
        json.dump(new_catalog, f, indent=2, ensure_ascii=False)

    print(f"Successfully saved catalog.json with:")
    print(f"- {len(subscriptions)} Subscriptions & Gift Cards")
    print(f"- {len(games)} Video Games")
    print(f"- {len(hardware_and_accessories)} Hardware & Accessories")
    print(f"- {len(other_items)} Other Items")
    print("=== CATALOG SYNC COMPLETE ===")

if __name__ == "__main__":
    main()
