from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from app.config import settings
from kiteconnect import KiteConnect
import logging, os, re

router = APIRouter()
logger = logging.getLogger(__name__)

# Path to the .env file so the token can be persisted across restarts
ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")


def _save_token_to_env(token: str):
    """Persist KITE_ACCESS_TOKEN to the .env file (upsert)."""
    try:
        content = ""
        if os.path.exists(ENV_FILE):
            with open(ENV_FILE) as f:
                content = f.read()
        if "KITE_ACCESS_TOKEN" in content:
            content = re.sub(r"KITE_ACCESS_TOKEN=.*", f"KITE_ACCESS_TOKEN={token}", content)
        else:
            content = content.rstrip("\n") + f"\nKITE_ACCESS_TOKEN={token}\n"
        with open(ENV_FILE, "w") as f:
            f.write(content)
        logger.info("KITE_ACCESS_TOKEN saved to .env")
    except Exception as e:
        logger.warning("Could not save token to .env: %s", e)


@router.get("/kite/login")
def kite_login():
    kite = KiteConnect(api_key=settings.KITE_API_KEY)
    login_url = kite.login_url()
    return JSONResponse({"login_url": login_url})


@router.get("/kite/callback", response_class=HTMLResponse)
def kite_callback(request: Request):
    params     = dict(request.query_params)
    status     = params.get("status")
    req_token  = params.get("request_token")

    if status != "success" or not req_token:
        return HTMLResponse(_error_page("Kite login failed or request_token missing."), status_code=400)

    try:
        kite = KiteConnect(api_key=settings.KITE_API_KEY)
        data = kite.generate_session(req_token, api_secret=settings.KITE_API_SECRET)
        access_token = data.get("access_token", "")
        user_name    = data.get("user_name", "")
        user_id      = data.get("user_id", "")

        # Auto-persist to .env so the Java service can also pick it up
        _save_token_to_env(access_token)
        # Also update the running settings object
        settings.KITE_ACCESS_TOKEN = access_token

        return HTMLResponse(_success_page(access_token, user_name, user_id))
    except Exception as e:
        logger.exception("Kite session generation failed")
        return HTMLResponse(_error_page(str(e)), status_code=500)


def _success_page(token: str, name: str, uid: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Kite Connected ✓</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0f1e;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:1rem}}
    .card{{background:#111827;border:1px solid #1e3a5f;border-radius:1.5rem;padding:2.5rem;max-width:640px;width:100%;box-shadow:0 25px 50px rgba(0,0,0,.5)}}
    .badge{{display:inline-flex;align-items:center;gap:.5rem;background:#052e16;border:1px solid #166534;color:#4ade80;border-radius:2rem;padding:.4rem 1rem;font-size:.8rem;font-weight:700;margin-bottom:1.5rem}}
    .dot{{width:.6rem;height:.6rem;background:#4ade80;border-radius:50%;animation:pulse 1.5s infinite}}
    @keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
    h1{{font-size:1.5rem;font-weight:800;color:#f1f5f9;margin-bottom:.3rem}}
    .subtitle{{color:#64748b;font-size:.85rem;margin-bottom:2rem}}
    .token-box{{background:#030712;border:1px solid #1e3a5f;border-radius:.75rem;padding:1rem 1.25rem;margin-bottom:1rem}}
    .token-label{{font-size:.7rem;font-weight:700;color:#38bdf8;text-transform:uppercase;letter-spacing:.08em;margin-bottom:.5rem}}
    .token-value{{font-family:'Courier New',monospace;font-size:.8rem;color:#e2e8f0;word-break:break-all;line-height:1.6}}
    .copy-btn{{background:#1d4ed8;color:#fff;border:none;border-radius:.5rem;padding:.5rem 1.25rem;font-size:.8rem;font-weight:700;cursor:pointer;margin-top:.75rem;transition:background .2s}}
    .copy-btn:hover{{background:#2563eb}}
    .copy-btn.copied{{background:#166534}}
    .step{{background:#0f172a;border:1px solid #1e293b;border-radius:.75rem;padding:1rem 1.25rem;margin-bottom:.75rem}}
    .step-num{{color:#94a3b8;font-size:.75rem;font-weight:700;margin-bottom:.3rem}}
    .step-text{{font-size:.85rem;color:#cbd5e1}}
    code{{background:#1e293b;color:#38bdf8;padding:.1rem .4rem;border-radius:.3rem;font-size:.8rem;font-family:'Courier New',monospace}}
    .actions{{display:flex;gap:.75rem;flex-wrap:wrap;margin-top:1.5rem}}
    .btn{{display:inline-flex;align-items:center;gap:.4rem;padding:.6rem 1.25rem;border-radius:.6rem;font-size:.85rem;font-weight:700;text-decoration:none;border:none;cursor:pointer;transition:all .2s}}
    .btn-primary{{background:#7c3aed;color:#fff}}.btn-primary:hover{{background:#6d28d9}}
    .btn-secondary{{background:#1e293b;color:#94a3b8;border:1px solid #334155}}.btn-secondary:hover{{color:#e2e8f0;border-color:#4b5563}}
    .info-row{{display:flex;gap:1.5rem;margin-bottom:1.5rem;flex-wrap:wrap}}
    .info-item{{flex:1;min-width:120px;background:#0f172a;border:1px solid #1e293b;border-radius:.75rem;padding:.75rem 1rem}}
    .info-key{{font-size:.7rem;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:.05em}}
    .info-val{{font-size:.95rem;font-weight:700;color:#f1f5f9;margin-top:.2rem}}
    .saved-note{{background:#052e16;border:1px solid #166534;border-radius:.6rem;padding:.6rem 1rem;font-size:.78rem;color:#4ade80;margin-top:.75rem}}
  </style>
</head>
<body>
<div class="card">
  <div class="badge"><span class="dot"></span> Authentication Successful</div>
  <h1>Kite Connected 🎉</h1>
  <p class="subtitle">Welcome back{', ' + name if name else ''}! Your NSE Delta System is now authorized.</p>

  <div class="info-row">
    <div class="info-item"><div class="info-key">User ID</div><div class="info-val">{uid or '—'}</div></div>
    <div class="info-item"><div class="info-key">User Name</div><div class="info-val">{name or '—'}</div></div>
    <div class="info-item"><div class="info-key">Status</div><div class="info-val" style="color:#4ade80">✓ Live</div></div>
  </div>

  <div class="token-box">
    <div class="token-label">Access Token</div>
    <div class="token-value" id="token">{token}</div>
    <button class="copy-btn" onclick="copyToken()">📋 Copy Token</button>
    <div class="saved-note">✓ Token auto-saved to server .env — Python backend is already using it.</div>
  </div>

  <div style="margin:1.5rem 0">
    <div style="font-size:.8rem;font-weight:700;color:#94a3b8;margin-bottom:.75rem">NEXT STEPS</div>
    <div class="step">
      <div class="step-num">STEP 1 — Python Backend</div>
      <div class="step-text">✓ Done automatically. Token saved to <code>.env</code> and active in this session.</div>
    </div>
    <div class="step">
      <div class="step-num">STEP 2 — Java Delta Service (port 8090)</div>
      <div class="step-text">Run this once the Java service is started:
        <br><br><code>curl -X POST https://nse.shashr.com/delta-api/kite/access-token \<br>&nbsp;&nbsp;-H 'Content-Type: application/json' \<br>&nbsp;&nbsp;-d '{{{"access_token":"{token}"}}}' </code>
      </div>
    </div>
    <div class="step">
      <div class="step-num">STEP 3 — Load 3 months historical data</div>
      <div class="step-text"><code>curl -X POST https://nse.shashr.com/delta-api/historical/load?months=3</code></div>
    </div>
  </div>

  <div class="actions">
    <a href="/delta" class="btn btn-primary">⚡ Open Delta Dashboard</a>
    <a href="/" class="btn btn-secondary">← Back to Dashboard</a>
  </div>
</div>

<script>
function copyToken() {{
  const t = document.getElementById('token').textContent;
  navigator.clipboard.writeText(t).then(() => {{
    const btn = document.querySelector('.copy-btn');
    btn.textContent = '✓ Copied!';
    btn.classList.add('copied');
    setTimeout(() => {{ btn.textContent = '📋 Copy Token'; btn.classList.remove('copied'); }}, 2000);
  }});
}}
// Auto-set token on Java service if it's running
fetch('/delta-api/kite/access-token', {{
  method: 'POST',
  headers: {{'Content-Type': 'application/json'}},
  body: JSON.stringify({{access_token: '{token}'}})
}}).then(() => console.log('Java service token set')).catch(() => console.log('Java service not running yet'));
</script>
</body>
</html>"""


def _error_page(msg: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Kite Auth Failed</title>
<style>body{{font-family:sans-serif;background:#0a0f1e;color:#e2e8f0;display:flex;align-items:center;justify-content:center;min-height:100vh}}
.card{{background:#111827;border:1px solid #7f1d1d;border-radius:1.5rem;padding:2rem;max-width:500px;width:90%}}
h1{{color:#f87171;margin-bottom:1rem}}p{{color:#94a3b8;margin-bottom:1.5rem}}
a{{color:#60a5fa;text-decoration:none}}</style></head>
<body><div class="card"><h1>❌ Authentication Failed</h1>
<p>{msg}</p>
<a href="https://kite.trade/connect/login?api_key=xcnose6lzs8vuzik&v=3">← Try login again</a>
</div></body></html>"""
