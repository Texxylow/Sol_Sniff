import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
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

# API routes registered FIRST — before static mount
app.include_router(router, prefix="/api")

# Serve frontend folder at root "/"
# Works whether Render runs from repo root or backend/ folder
BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"

# Fallback: if frontend not found relative to parent, try sibling
if not FRONTEND_DIR.exists():
    FRONTEND_DIR = BASE_DIR / "frontend"

app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
