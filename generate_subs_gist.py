import os
import json
import base64
import requests
from collections import defaultdict
import datetime

# --- 配置常量 ---
USERS_DIR = 'users'
SAVE_FILE = 'SUBSCRIPTIONS.txt'  # 本地保存的文件名


def get_config():
    """
    获取配置（仅通过环境变量，避免依赖 .env 文件）
    必需：GITHUB_TOKEN, GIST_ID, GITHUB_USER, SUBS_BASE_URL
    可选：SUBS_TOKEN
    """
    token = os.environ.get('GITHUB_TOKEN')
    gist_id = os.environ.get('GIST_ID')
    gh_user = os.environ.get('GITHUB_USER')
    subs_base_url = os.environ.get('SUBS_BASE_URL')
    subs_token = os.environ.get('SUBS_TOKEN') or ''

    if not token or str(token).startswith("ghp_xxxx"):
        print("❌ 错误: 未找到有效的 GITHUB_TOKEN，请通过环境变量 GITHUB_TOKEN 提供。")
        exit(1)
    if not gist_id:
        print("❌ 错误: 未找到 GIST_ID，请通过环境变量 GIST_ID 提供。")
        exit(1)
    if not gh_user:
        print("❌ 错误: 未找到 GITHUB_USER，请通过环境变量 GITHUB_USER 提供。")
        exit(1)
    if not subs_base_url:
        print("❌ 错误: 未找到 SUBS_BASE_URL，请通过环境变量 SUBS_BASE_URL 提供（例如 https://subs.example.com）。")
        exit(1)

    return token, gist_id, gh_user, subs_base_url.rstrip('/'), subs_token

# --- 初始化配置 ---
GITHUB_TOKEN, GIST_ID, GITHUB_USER, SUBS_BASE_URL, SUBS_TOKEN = get_config()

def update_gist(files_content):
    """更新 Gist"""
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    payload = {"files": files_content}
    try:
        response = requests.patch(url, headers=headers, json=payload)
        response.raise_for_status()
        print(f"✅ Gist 更新成功! ID: {GIST_ID}")
        return True
    except Exception as e:
        print(f"❌ Gist 更新失败: {e}")
        return False

def generate_subscriptions():
    user_links = defaultdict(list)

    # 1. 读取本地节点信息
    if not os.path.exists(USERS_DIR):
        print(f"⚠️ 警告: 目录 {USERS_DIR} 不存在。")
        return

    for filename in os.listdir(USERS_DIR):
        if not filename.endswith('.json'): continue
        try:
            parts = filename.rsplit('_', 1)
            if len(parts) != 2: continue
            username = parts[0]
            
            filepath = os.path.join(USERS_DIR, filename)
            with open(filepath, 'r') as f:
                data = json.load(f)
                for node in data:
                    if 'subscription' in node:
                        user_links[username].append(node['subscription'])
        except Exception:
            pass

    # 2. 准备数据
    gist_files = {}
    output_lines = []
    
    # 添加文件头
    header = f"Generate Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n" + "="*60
    output_lines.append(header)
    print(f"正在处理 {len(user_links)} 个用户的订阅...")
    
    for user, links in user_links.items():
        # 聚合链接并 Base64 编码 (用于 Gist 内容)
        content_raw = '\n'.join(links)
        encoded_content = base64.b64encode(content_raw.encode('utf-8')).decode('utf-8')
        
        # 文件名使用 UUID
        file_id = user  # 使用用户名作为文件名，避免 UUID 难记
        gist_files[file_id] = {"content": encoded_content}
        
        # 生成订阅链接（走 monitor 代理，保持域名一致；token 可选）
        token_suffix = f"?token={SUBS_TOKEN}" if SUBS_TOKEN else ""
        sub_url = f"{SUBS_BASE_URL}/{file_id}{token_suffix}"
        
        # 记录日志
        log_line = f"用户: {user:<15} 订阅链接: {sub_url}"
        print(log_line) # 打印到屏幕
        output_lines.append(log_line) # 添加到文件缓存

    # 3. 推送更新
    if gist_files:
        success = update_gist(gist_files)
        if success:
            # 4. 【核心修改】只有 Gist 更新成功了，才写入本地文件
            try:
                with open(SAVE_FILE, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(output_lines))
                print(f"💾 订阅链接已保存到本地文件: {os.path.abspath(SAVE_FILE)}")
            except Exception as e:
                print(f"⚠️ 保存本地文件失败: {e}")
    else:
        print("没有需要更新的内容。")

if __name__ == '__main__':
    generate_subscriptions()
