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

# Serve the frontend folder at root "/"
# Render runs from the repo root, so frontend/ is one level up from backend/
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
