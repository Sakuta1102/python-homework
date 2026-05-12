import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from routers import clean
from routers import pipeline
from routers.feishu_auth import router as feishu_auth_router

app = FastAPI(
    title="自动化数据清洗服务",
    description="基于 FastAPI 的自动化数据清洗平台，支持 CSV / Excel / JSON 文件上传与清洗",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(clean.router)
app.include_router(pipeline.router)
app.include_router(feishu_auth_router)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", tags=["ui"], include_in_schema=False)
def index():
    return FileResponse("static/index.html")


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok", "message": "数据清洗服务运行中"}
