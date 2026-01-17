import os
import shutil
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

import subscription  # 导入上面的模块

load_dotenv()  # 自动加载 .env 文件

app = FastAPI()

# 从环境变量获取 Admin Token (n8n调用验证)
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_VENV_BIN = PROJECT_ROOT / ".venv" / "bin"
ADMIN_VENV_BIN = Path(os.getenv("ADMIN_VENV_BIN", DEFAULT_VENV_BIN))
ADMIN_PYTHON = Path(os.getenv("ADMIN_PYTHON", ADMIN_VENV_BIN / "python3"))
ADMIN_ANSIBLE = Path(os.getenv("ADMIN_ANSIBLE", ADMIN_VENV_BIN / "ansible-playbook"))
SUBS_BASE_URL = os.getenv("SUBS_BASE_URL")


def _pick_cmd(path_candidate: Path, fallback: str) -> str:
    """优先使用虚拟环境中的命令，不存在则回退到 PATH。"""
    if path_candidate.exists():
        return str(path_candidate)
    discovered = shutil.which(fallback)
    return discovered or fallback


PYTHON_CMD = _pick_cmd(ADMIN_PYTHON, "python3")
ANSIBLE_CMD = _pick_cmd(ADMIN_ANSIBLE, "ansible-playbook")
WORKDIR = Path(os.getenv("ADMIN_WORKDIR", PROJECT_ROOT))


def _require_config(value: str, name: str):
    if not value:
        raise HTTPException(500, f"未配置 {name}，请设置环境变量 {name}")
    return value

class UserReq(BaseModel):
    name: str
    level: int = 10
    days: int = 30

def verify_admin(authorization: str = Header(None)):
    _require_config(ADMIN_TOKEN, "ADMIN_TOKEN")
    if authorization != f"Bearer {ADMIN_TOKEN}":
        raise HTTPException(403, "Invalid Token")

def run_ansible_deploy():
    # 极速部署：只跑 users tag
    try:
        subprocess.run(
            [ANSIBLE_CMD, "deploy.yml", "--tags", "users"],
            cwd=WORKDIR,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"Ansible deploy failed: {exc}")

# --- 1. 管理接口 (给 n8n/Bot 用) ---
@app.post("/api/user/update", dependencies=[Depends(verify_admin)])
async def update_user(req: UserReq, bg: BackgroundTasks):
    # 1. 确保用户文件存在
    if not subscription.load_user(req.name)[0]:
        try:
            subprocess.run(
                [PYTHON_CMD, "generate_user.py", "add", req.name],
                cwd=WORKDIR,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise HTTPException(500, f"创建用户失败: {exc}") from exc
    
    # 2. 更新等级/时间
    ok, data = subscription.add_subscription(req.name, req.level, req.days)
    if not ok: raise HTTPException(500, data)
    
    # 3. 异步触发 Ansible
    bg.add_task(run_ansible_deploy)
    
    # 4. 返回链接
    # 注意：需配置 cloudflared 指向本地 8000
    subs_base = _require_config(SUBS_BASE_URL, "SUBS_BASE_URL").rstrip("/")
    sub_link = f"{subs_base}/feed/{data.get('sub_token')}"
    return {"status": "ok", "expire": data["expire_at"], "link": sub_link}

# --- 2. 新订阅接口 (安全, Bearer Token) ---
@app.get("/feed/{token}", response_class=PlainTextResponse)
async def get_feed(token: str):
    user = subscription.get_user_by_token(token)
    if not user: raise HTTPException(404, "Invalid Subscription")
    # 生成 VLESS 链接
    links = subscription.generate_vless_links(user)
    return links

# --- 3. 旧订阅接口 (兼容老用户) ---
@app.get("/subs", response_class=PlainTextResponse)
async def get_legacy_subs(user: str):
    # 保留原有逻辑，供存量用户使用
    _path, data = subscription.load_user(user)
    if not data:
        raise HTTPException(404, "User not found")
    
    links = subscription.generate_vless_links(data)
    return links
