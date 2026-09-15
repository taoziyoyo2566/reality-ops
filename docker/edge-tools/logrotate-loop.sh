#!/bin/sh
# 按固定间隔运行 logrotate；logrotate 的 maxsize 只在运行时检查，daily 由状态文件判断。
# 用法：logrotate-loop <配置文件> <状态文件> <间隔秒数>
set -u
conf="$1"
state="$2"
interval="${3:-3600}"

trap 'exit 0' TERM INT
while :; do
    if ! logrotate -s "$state" "$conf"; then
        echo "logrotate-loop: logrotate exited with an error" >&2
    fi
    # sleep 在后台等待，使 TERM 能立即结束容器而不必等满间隔。
    sleep "$interval" &
    wait $!
done
