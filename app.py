from __future__ import annotations

import os
import secrets
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict

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
sessions: Dict[str, Dict[str, Any]] = {}

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
        <button id="create">Generate Shareable Link</button>
        <div id="result" class="notice" style="margin-top:1rem;"></div>
    </div>
    <script>
    const button = document.getElementById("create");
    const result = document.getElementById("result");
    async function createLink() {
        button.disabled = true;
        result.textContent = "Creating link...";
        try {
            const response = await fetch("/api/session", { method: "POST" });
            if (!response.ok) {
                throw new Error("Server responded with " + response.status);
            }
            const data = await response.json();
            result.innerHTML = `
                Share this link with the participant:<br>
                <a href="${data.link}" target="_blank" rel="noopener">${data.link}</a><br><br>
                Monitor their updates here:<br>
                <a href="${data.monitor}" target="_blank" rel="noopener">${data.monitor}</a>
            `;
        } catch (error) {
            result.textContent = "Failed to create link: " + error.message;
        } finally {
            button.disabled = false;
        }
    }
    button.addEventListener("click", createLink);
    </script>
</body>
</html>
"""

TRACK_HTML = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <title>Share Camera & Location</title>
    <style>
        body { font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; margin: 2rem; }
        video { width: 100%; max-width: 480px; border-radius: 10px; background: #000; }
        .status { margin-top: 1rem; font-size: 0.95rem; color: #243b53; }
    </style>
</head>
<body>
    <h1>Live sharing session</h1>
    <p>To proceed you must explicitly allow your browser to access the camera and your approximate location.
    You can stop sharing at any time by closing this tab.</p>
    <video id="preview" autoplay playsinline muted></video>
    <div class="status" id="status">Waiting for permission…</div>
    <script>
    const updateUrl = {{ update_url|tojson }};
    const statusEl = document.getElementById("status");
    const video = document.getElementById("preview");
    const canvas = document.createElement("canvas");
    let stream = null;

    function setStatus(text) {
        statusEl.textContent = text;
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
            setStatus("Last update sent at " + new Date().toLocaleTimeString());
        } catch (error) {
            setStatus("Could not send update: " + error.message);
        }
    }

    function startLocationWatch() {
        if (!navigator.geolocation) {
            setStatus("Geolocation API not available in this browser.");
            return;
        }
        navigator.geolocation.watchPosition(
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
                setStatus("Location error: " + error.message);
            },
            { enableHighAccuracy: true, maximumAge: 10000, timeout: 60000 }
        );
    }

    async function captureFrame() {
        if (!stream) {
            return;
        }
        const trackSettings = stream.getVideoTracks()[0]?.getSettings() || {};
        const width = video.videoWidth || trackSettings.width || 640;
        const height = video.videoHeight || trackSettings.height || 480;
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(video, 0, 0, width, height);
        const frame = canvas.toDataURL("image/jpeg", 0.6);
        sendUpdate({ frame });
    }

    async function start() {
        try {
            stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
            video.srcObject = stream;
            setStatus("Camera live. Requesting location…");
            startLocationWatch();
            setInterval(captureFrame, 4000);
        } catch (error) {
            setStatus("Camera permission denied or unavailable: " + error.message);
        }
    }

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        setStatus("Camera API not available in this browser.");
    } else {
        start();
    }
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


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_session(token: str) -> Dict[str, Any]:
    session = sessions.get(token)
    if not session:
        abort(404, description="Unknown session token")
    return session


@app.get("/")
def home():
    return render_template_string(HOME_HTML)


@app.post("/api/session")
def create_session():
    token = secrets.token_urlsafe(8)
    sessions[token] = {
        "token": token,
        "created": iso_now(),
        "updates": deque(maxlen=MAX_UPDATES),
    }
    return jsonify(
        {
            "token": token,
            "link": url_for("track_page", token=token, _external=True),
            "monitor": url_for("monitor_page", token=token, _external=True),
        }
    )


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
    entry: Dict[str, Any] = {"ts": iso_now()}

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
    session["last_seen"] = entry["ts"]

    return jsonify({"status": "ok"})


@app.get("/api/status/<token>")
def session_status(token: str):
    session = ensure_session(token)
    updates: Deque[Dict[str, Any]] = session["updates"]  # type: ignore[assignment]
    latest = updates[-1] if updates else None
    return jsonify(
        {
            "token": token,
            "created": session["created"],
            "last_seen": session.get("last_seen"),
            "history_count": len(updates),
            "latest": latest,
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
