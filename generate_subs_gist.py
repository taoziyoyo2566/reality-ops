import os
import json
import base64
import requests
from collections import defaultdict

# --- 配置 ---
# 建议将这些放在 Ansible 的环境变量或 Vault 中，不要硬编码
GITHUB_TOKEN = "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"
GIST_ID = "0a7faae910b06151d643c7d5baa43818"
# -----------

USERS_DIR = 'users'

def update_gist(files_content):
    """
    使用 GitHub API 更新 Gist
    API 文档: https://docs.github.com/en/rest/gists/gists?apiVersion=2022-11-28#update-a-gist
    """
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    
    # Gist API 的格式： {"files": {"文件名": {"content": "内容"}}}
    # 如果 content 为 null，则删除该文件
    payload = {"files": files_content}
    
    try:
        response = requests.patch(url, headers=headers, json=payload)
        response.raise_for_status()
        print(f"✅ Gist 更新成功! ID: {GIST_ID}")
    except Exception as e:
        print(f"❌ Gist 更新失败: {e}")
        if response:
            print(response.text)

def generate_subscriptions():
    user_links = defaultdict(list)
    user_uuids = {} 

    # 1. 读取本地节点信息 (逻辑不变)
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

    # 2. 准备上传到 Gist 的数据
    gist_files = {}
    
    print(f"正在处理 {len(user_links)} 个用户的订阅...")
    
    for user, links in user_links.items():
        # 聚合链接并 Base64 编码
        content_raw = '\n'.join(links)
        encoded_content = base64.b64encode(content_raw.encode('utf-8')).decode('utf-8')
        
        # 文件名使用 UUID (起到 Token 保护作用)
        file_uuid = user_uuids.get(user, user)
        
        # 添加到 payload
        gist_files[file_uuid] = {"content": encoded_content}
        
        # 打印出最终的订阅链接
        # 注意：Gist Raw 链接格式通常是 https://gist.githubusercontent.com/<user>/<id>/raw/<filename>
        # 但为了保证拿到最新版，通常省略中间的 commit hash，或者直接用 api 这里的 hack 方式
        print(f"用户 {user} 订阅链接: https://gist.githubusercontent.com/{os.getenv('GITHUB_USER', 'taoziyoyo2566')}/{GIST_ID}/raw/{file_uuid}")

    # 3. 推送更新
    if gist_files:
        update_gist(gist_files)

if __name__ == '__main__':
    generate_subscriptions()