import sys
import traceback
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

try:
    from scanner import router
    print("scanner.py imported OK")
except Exception as e:
    print("IMPORT ERROR:", e)
    traceback.print_exc()
    sys.exit(1)

app = FastAPI(title="SolSniff API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")

FRONTEND_DIR = Path(file).resolve().parent.parent / "frontend"

try:
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    print("Frontend mounted OK at:", FRONTEND_DIR)
except Exception as e:
    print("FRONTEND MOUNT ERROR:", e)
    traceback.print_exc()
