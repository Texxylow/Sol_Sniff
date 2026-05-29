from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from scanner import router

app = FastAPI(title="SolSniff API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routes (must be registered BEFORE static mount) ──────────────────────
app.include_router(router, prefix="/api")

# ── Serve frontend static files ──────────────────────────────────────────────
FRONTEND_DIR = Path(__file__).parent

# Serve everything in /frontend (css, js, images) at root
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
