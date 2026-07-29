#!/usr/bin/env bash
set -e

# 确保能找到 /sbin 下的 tc/ip
PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

normalize_rate() {
  # 统一速率格式，兼容纯数字/mbps/kbps写法
  local r
  r=$(echo "$1" | tr '[:upper:]' '[:lower:]')
  if [[ $r =~ ^[0-9]+$ ]]; then
    echo "${r}mbit"
    return 0
  fi
  if [[ $r =~ ^[0-9]+(k|m|g)?bit$ ]]; then
    echo "$r"
    return 0
  fi
  if [[ $r =~ ^[0-9]+(k|m|g)?bps$ ]]; then
    echo "${r%bps}bit"
    return 0
  fi
  return 1
}

# ================= 参数校验 =================
if [ $# -ne 2 ]; then
  echo "用法: $0 <docker_container> <rate>"
  echo "示例: $0 reality_qin_XH73YJ08 5mbit"
  exit 1
fi

CONTAINER="$1"
RAW_RATE="$2"
RATE=$(normalize_rate "$RAW_RATE") || {
  echo "❌ 无效速率: $RAW_RATE（示例: 5mbit / 500kbit / 10gbit）"
  exit 1
}

# ================= 权限与依赖检查 =================
if [ "$EUID" -ne 0 ]; then
  if ! command -v sudo >/dev/null 2>&1; then
    echo "❌ 需要 root 权限（未找到 sudo）"
    exit 1
  fi
  SUDO="sudo "
else
  SUDO=""
fi

for cmd in docker nsenter ip tc; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "❌ 缺少命令: $cmd"
    exit 1
  fi
done

# ================= 网络模式提示 =================
NETWORK_MODE=$(docker inspect --format '{{.HostConfig.NetworkMode}}' "$CONTAINER" 2>/dev/null || true)

# ================= 获取容器 PID =================
PID=$(docker inspect --format '{{.State.Pid}}' "$CONTAINER" 2>/dev/null || true)

if [ -z "$PID" ] || [ "$PID" = "0" ]; then
  echo "❌ 无法获取容器 PID，容器可能未运行"
  exit 1
fi

# ================= 从 netns 读取 eth0 的 iflink =================
IFLINK=$(${SUDO}nsenter -t "$PID" -n cat /sys/class/net/eth0/iflink 2>/dev/null || true)

if [ -z "$IFLINK" ]; then
  echo "❌ 无法获取 eth0 iflink（netns 不可用？）"
  exit 1
fi

# ================= 通过 ifindex 反查宿主机 veth =================
VETH=$(ip -o link | awk -F': ' -v idx="$IFLINK" '$1==idx {print $2}' | cut -d'@' -f1)

if [ -z "$VETH" ]; then
  echo "❌ 无法找到宿主机 veth 接口（ifindex=$IFLINK）"
  exit 1
fi

if [[ "$NETWORK_MODE" == "host" || "$VETH" == "eth0" ]] && [ -z "$ALLOW_NON_VETH" ]; then
  echo "❌ 容器使用 host 网络或解析到宿主接口 $VETH，避免误限宿主机。"
  echo "   如确定要限制该接口，设置 ALLOW_NON_VETH=1 后再执行。"
  exit 1
fi

if [[ "$VETH" != veth* && -z "$ALLOW_NON_VETH" ]]; then
  echo "❌ 解析到的接口 $VETH 不是 veth*，可能是 macvlan/host。"
  echo "   若确认就是要限制该接口，请设置 ALLOW_NON_VETH=1。"
  exit 1
fi

echo "✔ 容器: $CONTAINER"
echo "✔ PID: $PID"
echo "✔ veth: $VETH"
echo "✔ 限速: $RATE"

# ================= 清理旧规则 =================
${SUDO}tc qdisc del dev "$VETH" root 2>/dev/null || true

# ================= Reality 推荐限速：HTB + fq_codel =================
# default 指向 1:1（唯一的类），否则未分类流量会被丢弃
${SUDO}tc qdisc add dev "$VETH" root handle 1: htb default 1
${SUDO}tc class add dev "$VETH" parent 1:1 htb rate "$RATE" ceil "$RATE"
${SUDO}tc qdisc add dev "$VETH" parent 1:1 handle 10: fq_codel

echo "✅ 限速已成功应用"
