from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from scanner import router
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from scanner import router as scanner_router

app = FastAPI(title="SolSniff API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# ── Access gate — server-side ─────────────────────────────────────────────────
ACCESS_CODE = "NEZER"
MAX_USES    = 10  # ← raise this number whenever you want more slots

_registry: dict = {}  # { email: True }

class AccessRequest(BaseModel):
    email: str
    code:  str

@app.post("/api/access/verify")
async def verify_access(req: AccessRequest):
    email = req.email.strip().lower()
    code  = req.code.strip().upper()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address.")
    if code != ACCESS_CODE:
        raise HTTPException(status_code=403, detail="Invalid access code.")
    if email in _registry:
        return {"status": "ok", "returning": True}
    if len(_registry) >= MAX_USES:
        raise HTTPException(status_code=429, detail=f"Access limit reached ({MAX_USES} slots full). Contact Ebenezer.")
    _registry[email] = True
    return {"status": "ok", "returning": False, "slots_used": len(_registry)}

@app.get("/api/access/status")
async def access_status():
    return {"used": len(_registry), "max": MAX_USES}

# ── Scanner + frontend ────────────────────────────────────────────────────────
app.include_router(scanner_router, prefix="/api")

FRONTEND_DIR = Path(_file_).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
# Serve everything in /frontend (css, js, images) at root
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
