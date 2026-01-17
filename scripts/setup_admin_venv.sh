#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV:-${ROOT}/.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
REQ_FILE="${ROOT}/requirements-admin.txt"

if [ ! -f "${REQ_FILE}" ]; then
  echo "requirements-admin.txt 不存在，脚本中止。" >&2
  exit 1
fi

if [ ! -x "${VENV}/bin/python3" ]; then
  echo "创建虚拟环境到 ${VENV}"
  "${PYTHON_BIN}" -m venv "${VENV}"
fi

echo "升级 pip 并安装依赖..."
"${VENV}/bin/pip" install --upgrade pip
"${VENV}/bin/pip" install --upgrade -r "${REQ_FILE}"

echo "✅ 完成。虚拟环境在 ${VENV}"
echo "   记得在 systemd 单元里设置 ADMIN_TOKEN，ExecStart 指向 ${VENV}/bin/python3 -m uvicorn admin_server:app --host 127.0.0.1 --port 8000"
