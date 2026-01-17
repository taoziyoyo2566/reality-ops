# Admin API (FastAPI + Ansible)

在中控机上运行的管理 API，提供用户续费/生成订阅等接口。请在宿主机使用 venv 隔离依赖，并通过 systemd 托管。

## 快速部署
部署流程已完全自动化，通过 `admin.yml` playbook 完成。

1) **检查变量**
确保 `group_vars/all/main.yml` 和 `group_vars/all/vault.yml` 中以下变量已配置：
- `monitor.subs_base_url`: 订阅域名 (例如 `https://subs.your-domain.com`)
- `vault_monitor_admin_bearer_token`: 管理 API 的 Bearer Token

2) **运行 Playbook**
该 playbook 将会自动完成创建目录、复制文件、生成 `.env` 和部署 systemd 服务等所有步骤。
```bash
# 确保 inventory.ini 中的 spt 主机已配置为本地连接
# ansible_playbook -i inventory.ini admin.yml --check --diff --ask-vault-pass # 预演
ansible-playbook -i inventory.ini admin.yml --ask-vault-pass
```

3) **验证服务**
```bash
sudo systemctl status reality-admin.service
journalctl -u reality-admin.service -f
```

## 本地开发/调试
对于本地开发，`admin_server.py` 会自动加载项目根目录下的 `.env` 文件。您可以手动从 `.env.sample` 复制并填写，或运行一次 `admin.yml` playbook 来生成它。
```bash
# 确保 .env 文件存在
source .venv/bin/activate
uvicorn admin_server:app --host 127.0.0.1 --port 8000 --reload
```

## API 速览
- `POST /api/user/update` （需要 Header: `Authorization: Bearer <ADMIN_TOKEN>`）  
  Body 示例：`{"name": "alice", "level": 10, "days": 30}`  
  - 如用户不存在，会自动调用 `generate_user.py add <name>`  
  - 更新后后台异步执行 `ansible-playbook deploy.yml --tags users`（优先用 venv 中的 ansible）
- `GET /feed/{token}` 返回订阅内容（目前是占位，需按你的订阅生成逻辑填充）
- `GET /subs` 旧订阅接口（占位，按需实现）

## 运行时环境变量
- `ADMIN_TOKEN`：管理接口 Bearer Token（必填）。
- `SUBS_BASE_URL`：用于拼接订阅链接（必填，无默认值）。
- `ADMIN_VENV_BIN`：虚拟环境 bin 路径，默认 `<项目根>/.venv/bin`。
- `ADMIN_PYTHON`：手动指定 Python 解释器（可选）。
- `ADMIN_ANSIBLE`：手动指定 ansible-playbook 路径（可选）。
- `ADMIN_WORKDIR`：执行 ansible/generate_user 的工作目录，默认项目根。

## 依赖文件
- `requirements-admin.txt`：pip 依赖清单（已包含 ansible、fastapi、uvicorn、cryptography 等）。
- `scripts/setup_admin_venv.sh`：一键创建/升级 venv。
- `systemd/reality-admin.service`：systemd 单元模板，记得修改路径和 Token。
