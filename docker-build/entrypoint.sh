#!/bin/sh
set -o pipefail

# 主函数
main() {
    # 检查外部挂载的配置文件是否存在
    # 因为我们在 Ansible 中使用了 -v 挂载，这里是一个很好的启动检查
    if [ ! -f /config.json ]; then
        echo "配置文件 /config.json 不存在，请检查挂载路径。退出。"
        exit 1
    fi

    echo "配置文件检测通过，正在启动 Xray..."
    # 运行 xray
    exec xray -config /config.json
}

# 执行主函数
main "$@"