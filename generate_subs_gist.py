import os
import json
import base64
import requests
from collections import defaultdict
import datetime

# --- 配置常量 ---
USERS_DIR = 'users'
ENV_FILE = '.env'
SAVE_FILE = 'SUBSCRIPTIONS.txt'  # 【新增】本地保存的文件名

def load_env_file():
    """简易版 .env 加载器"""
    env_vars = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'): continue
                if '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key.strip()] = value.strip().strip("'").strip('"')
    return env_vars

def get_config():
    """获取配置"""
    file_env = load_env_file()
    token = os.environ.get('GITHUB_TOKEN') or file_env.get('GITHUB_TOKEN')
    gist_id = os.environ.get('GIST_ID') or file_env.get('GIST_ID')
    # 获取 GitHub 用户名，用于拼接 raw 链接，如果没配置则用默认值
    gh_user = os.environ.get('GITHUB_USER') or file_env.get('GITHUB_USER') or 'taoziyoyo2566'

    if not token or str(token).startswith("ghp_xxxx"):
        print("❌ 错误: 未找到有效的 GITHUB_TOKEN，请检查 .env 文件。")
        exit(1)
    if not gist_id:
        print("❌ 错误: 未找到 GIST_ID，请检查 .env 文件。")
        exit(1)

    return token, gist_id, gh_user

# --- 初始化配置 ---
GITHUB_TOKEN, GIST_ID, GITHUB_USER = get_config()

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
    user_uuids = {} 

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
                    if 'id' in node:
                        user_uuids[username] = node['id']
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
        file_uuid = user_uuids.get(user, user)
        gist_files[file_uuid] = {"content": encoded_content}
        
        # 生成订阅链接
        sub_url = f"https://gist.githubusercontent.com/{GITHUB_USER}/{GIST_ID}/raw/{file_uuid}"
        
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