# 🎮 PlayStation World Multan - WhatsApp AI Agent

An intelligent, bilingual (Roman Urdu & English) WhatsApp AI Agent and management control center built specifically for **PlayStation World Multan** (deals in PlayStation & Xbox consoles, original games, PS Plus subscriptions, and Xbox Game Pass codes).

---

## 🌟 Key Features

1. **Warm & Natural Customer Welcoming**:
   - Welcomes buyers warmly on behalf of *PlayStation World Multan*.
   - Understands inquiries in **Roman Urdu** (*"Bhai PS Plus Extra 1 month code kitne ka hai?"*) and **English** (*"Do you have FC 25 PS5 in stock?"*) and matches customer language effortlessly.

2. **Specialized Gaming & Subscription Knowledge**:
   - **PlayStation Plus**: Essential, Extra, and Deluxe tiers with up-to-date PKR prices and region details (USA, UAE, Turkey).
   - **Xbox Game Pass**: Ultimate, Core, PC Game Pass, and wallet gift cards.
   - **Consoles & Hardware**: PS5 Slim (Disc/Digital), PS4 Pro/Slim, Xbox Series X/S, DualSense controllers, and PS4 jailbreak game filling.
   - **Digital Delivery Policy**: Instant code dispatch within 5–15 minutes upon payment screenshot confirmation (JazzCash / EasyPaisa / Bank Transfer).
   - **Multan Store Pickup & Nationwide Delivery**: In-store cash pickup in Multan or courier delivery across Pakistan.

3. **Official Meta WhatsApp Cloud API Integration**:
   - Webhook endpoint (`/webhook`) with instant async processing to satisfy Meta's latency rules.
   - Automatic message read receipts (blue tick indicator).

4. **Built-in WhatsApp Web Simulator**:
   - Test conversation flows, product inquiries, and prompt responses directly in your web browser without needing to configure WhatsApp first.

5. **Live Control Center & Catalog Manager**:
   - Dynamic `catalog.json` management with instant reflection in the AI's answers.
   - Real-time conversation logs.

---

## 🚀 Quick Start

### 1. Launch the Server
Double-click `run.bat` or run:
```bash
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Open your browser at: **[http://localhost:8000](http://localhost:8000)**

---

## 🔑 Configuration (.env)

Open `.env` or use the **WhatsApp Cloud Setup** tab in the web dashboard:

```env
# Google Gemini API Key (Get free key from https://aistudio.google.com)
GEMINI_API_KEY=AIzaSy...
GEMINI_MODEL=gemini-2.5-flash

# Meta WhatsApp Cloud API
WHATSAPP_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_VERIFY_TOKEN=playstation123
WHATSAPP_API_VERSION=v21.0

# Shop Settings
SHOP_NAME=PlayStation World Multan
HOST=0.0.0.0
PORT=8000
```

---

## 📲 How to Connect Real WhatsApp (Meta WhatsApp Cloud API)

1. Go to [Meta for Developers](https://developers.facebook.com) and create a **Business App**.
2. Add **WhatsApp** to your app.
3. In your development environment, start an HTTPS tunnel (e.g., `ngrok http 8000`).
4. In Meta WhatsApp Dashboard &gt; **Configuration**:
   - **Callback URL**: `https://<your-ngrok-url>.ngrok-free.app/webhook`
   - **Verify Token**: `playstation_world_multan_secret_token_2025`
5. Click **Verify and Save**.
6. Under **Webhook fields**, click **Manage** and subscribe to **`messages`**.
7. Copy your **Phone Number ID** and **Access Token** into `.env`.
8. Send a message to your WhatsApp test number and watch the bot reply automatically!

---

## 📁 Project Structure

```
Playstation world AI/
├── catalog.json          # PlayStation & Xbox products, subscriptions & prices
├── config.py             # Environment & settings loader
├── gemini_agent.py       # Roman Urdu/English AI agent engine
├── whatsapp_client.py    # Meta Graph API sender & read receipts
├── main.py               # FastAPI server & Meta webhooks
├── run.bat               # 1-click startup launcher
├── requirements.txt      # Python dependencies
├── static/
│   ├── index.html        # Control center & WhatsApp simulator UI
│   ├── style.css         # Gaming theme styling
│   └── app.js            # Frontend interactivity
└── .env                  # API keys and configuration
```
