#!/usr/bin/env python3
import json
import os
import secrets
import datetime
import configparser
from pathlib import Path
from urllib.parse import urlencode

USERS_DIR = "users"
DATE_FORMAT = "%Y-%m-%d"
REALITY_SERVER_NAMES = ["www.apple.com", "images.apple.com"]  # 默认 SNI

def _parse_inventory():
    """解析 Ansible inventory，返回 (主机, 等级) 列表。"""
    inventory = configparser.ConfigParser(allow_no_value=True)
    # 不区分大小写
    inventory.optionxform = str
    
    # 寻找 inventory.ini
    # todo: 硬编码了，后面改成从 ansible.cfg 读取
    if not Path("inventory.ini").exists():
        print(" [W] inventory.ini not found, cannot generate links")
        return []
    
    inventory.read("inventory.ini")
    
    nodes = []
    if "reality_nodes" not in inventory:
        return []
        
    for host in inventory["reality_nodes"]:
        # host like: `dzire ansible_python_interpreter=/usr/bin/python3 level=10`
        parts = host.split()
        name = parts[0]
        
        level = 0
        for part in parts:
            if part.startswith("level="):
                level = int(part.split("=")[1])
        
        # 'spt' is a special host for monitor, skip it
        if name == "spt": continue
        
        nodes.append({"name": name, "level": level})
    return nodes
    
def generate_vless_links(user: dict):
    """为单个用户生成所有可用节点的 VLESS 链接。"""
    
    user_level = user.get("access_level", 0)
    if user_level == 0: return ""
    
    nodes = _parse_inventory()
    accessible_nodes = [n for n in nodes if n["level"] <= user_level]
    
    links = []
    for node in accessible_nodes:
        params = {
            "type": "tcp",
            "security": "reality",
            "flow": "xtls-rprx-vision",
            "sni": REALITY_SERVER_NAMES[0],
            "fp": "chrome",
            "pbk": user["public_key"],
            "sid": user["short_id"]
        }
        
        # vless://{uuid}@{domain}:{port}?{params}#name
        link = (
            f"vless://{user['uuid']}@{node['name']}:{user['port']}"
            f"?{urlencode(params)}"
            f"#{user['name']}-{node['name']}"
        )
        links.append(link)
        
    return "\n".join(links)

def load_user(name):
    # 兼容 .yml / .json
    base = Path(USERS_DIR)
    for ext in [".yml", ".json", ".yaml"]:
        path = base / f"{name}{ext}"
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return path, json.load(f)
    return None, None

def save_user(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def add_subscription(name, level, days):
    path, data = load_user(name)
    if not data: return False, "User not found"
    
    # 续费计算逻辑
    now = datetime.datetime.now()
    current_expire = data.get("expire_at")
    start_date = now
    
    # 如果未过期且等级不变，顺延
    if current_expire and data.get("access_level") == int(level):
        try:
            exp_date = datetime.datetime.strptime(current_expire, DATE_FORMAT)
            if exp_date > now: start_date = exp_date
        except: pass

    new_expire = start_date + datetime.timedelta(days=days)
    data["access_level"] = int(level)
    data["expire_at"] = new_expire.strftime(DATE_FORMAT)
    
    # 【新用户安全】生成专用订阅 Token
    if "sub_token" not in data:
        data["sub_token"] = secrets.token_urlsafe(24)
        
    save_user(path, data)
    return True, data

def get_user_by_token(token):
    # 遍历查找 (用户量<1000时性能无损耗)
    for f in Path(USERS_DIR).iterdir():
        if f.suffix in [".yml", ".json", ".yaml"]:
            try:
                with open(f) as fp:
                    data = json.load(fp)
                    if data.get("sub_token") == token: return data
            except: pass
    return None

def check_expiration():
    # 供定时任务调用：清理过期用户
    now = datetime.datetime.now()
    count = 0
    for f in Path(USERS_DIR).iterdir():
        if f.suffix not in ['.yml', '.json']: continue
        try:
            with open(f) as fp: data = json.load(fp)
            if not data.get("expire_at"): continue
            
            exp = datetime.datetime.strptime(data["expire_at"], DATE_FORMAT)
            if now > (exp + datetime.timedelta(days=1)): # 宽限1天
                if data.get("access_level", 0) != 0:
                    data["access_level"] = 0
                    del data["expire_at"] # 清除有效期
                    save_user(f, data)
                    count += 1
        except: pass
    return count