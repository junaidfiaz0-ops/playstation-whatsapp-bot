import sys
import io
import json

# Ensure utf-8 stdout on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from fastapi.testclient import TestClient
from main import app
import config

client = TestClient(app)

def test_webhook_verification():
    print("Testing GET /webhook verification...")
    res = client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": config.WHATSAPP_VERIFY_TOKEN,
            "hub.challenge": "test_challenge_12345"
        }
    )
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    assert res.text == "test_challenge_12345", f"Expected challenge string, got {res.text}"
    print("[PASS] Webhook verification passed!")

def test_webhook_verification_bad_token():
    print("Testing GET /webhook verification with bad token...")
    res = client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong_token",
            "hub.challenge": "12345"
        }
    )
    assert res.status_code == 403, f"Expected 403, got {res.status_code}"
    print("[PASS] Webhook bad token rejection passed!")

def test_status_endpoint():
    print("Testing GET /api/status...")
    res = client.get("/api/status")
    assert res.status_code == 200
    data = res.json()
    assert data["shop_name"] == "PlayStation World Multan"
    assert "catalog_stats" in data
    assert data["catalog_stats"]["subscriptions"] > 0
    assert data["catalog_stats"]["games"] > 0
    print(f"[PASS] Status: Catalog has {data['catalog_stats']['subscriptions']} subscription tiers, {data['catalog_stats']['games']} games.")

def test_catalog_endpoint():
    print("Testing GET /api/catalog...")
    res = client.get("/api/catalog")
    assert res.status_code == 200
    catalog = res.json()
    assert "shop_info" in catalog
    assert catalog["shop_info"]["name"] == "PlayStation World Multan"
    print("[PASS] Catalog loading passed!")

def test_chat_simulator_endpoint():
    print("Testing POST /api/chat/test...")
    res = client.post(
        "/api/chat/test",
        json={
            "session_id": "test_customer_03001234567",
            "message": "Bhai PS Plus Extra kitne ka hai?"
        }
    )
    assert res.status_code == 200
    data = res.json()
    assert "reply" in data
    print(f"[PASS] Chat simulator replied: {data['reply'][:90]}...")

def test_webhook_post_incoming():
    print("Testing POST /webhook incoming WhatsApp payload...")
    sample_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123456789",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550234567",
                                "phone_number_id": "1234567890"
                            },
                            "contacts": [
                                {
                                    "profile": {
                                        "name": "Ahmed Multan Gamer"
                                    },
                                    "wa_id": "923001234567"
                                }
                            ],
                            "messages": [
                                {
                                    "from": "923001234567",
                                    "id": "wamid.HBgLOTIzMDAxMjM0NTY3FQIAEhgg",
                                    "timestamp": "1710000000",
                                    "text": {
                                        "body": "AOA bhai, Xbox Game Pass 1 month ka code mil jaye ga?"
                                    },
                                    "type": "text"
                                }
                            ]
                        },
                        "field": "messages"
                    }
                ]
            }
        ]
    }
    res = client.post("/webhook", json=sample_payload)
    assert res.status_code == 200
    assert res.json() == {"status": "received"}
    print("[PASS] Webhook POST incoming message processing passed!")

if __name__ == "__main__":
    test_webhook_verification()
    test_webhook_verification_bad_token()
    test_status_endpoint()
    test_catalog_endpoint()
    test_chat_simulator_endpoint()
    test_webhook_post_incoming()
    print("\n>>> ALL TESTS PASSED SUCCESSFULLY! <<<")
