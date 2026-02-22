#!/usr/bin/env python3
"""
生成/删除/查看单个用户的配置文件 (users/<name>.yml)。
字段包含：name、uuid、port、short_id、private_key、public_key、groups、hosts。

使用说明:
- 依赖: 需要安装 cryptography。若系统无 pip，可用 `python3 -m ensurepip --default-pip`
  启用 pip；或使用发行版包管理器安装，例如 Debian/Ubuntu 下 `sudo apt install python3-cryptography`。
  也可以使用 pip: `python3 -m pip install cryptography`。
- 添加/覆盖: `python3 generate_user.py add <name>`（兼容老用法 `python3 generate_user.py <name>`）
  可选 `--port` 指定端口，`--groups/--hosts` 指定 ACL（逗号分隔，默认 all），`--min-port/--max-port` 控制端口池，`--users-dir` 指定输出目录。
- 删除: `python3 generate_user.py delete <name>`，删除 users 目录下对应的 yml/yaml/json。
- 查看: `python3 generate_user.py list`，默认只显示 yml/yaml，按文件汇总用户与端口；`--include-json` 会额外展示 json 文件（不展开数组）；`--details` 会展开 json 数组逐条显示并自动包含 json；目录不存在会自动创建。
- 用户名仅允许字母、数字、下划线和短横线，避免路径注入。
- 使用 Docker（无需本机安装依赖）: `python3 generate_user.py --docker add <name>`，
  默认镜像 python:3.11-slim，可用 `--docker-image` 指定。
- 查看帮助/示例: `python3 generate_user.py -h`
"""

import argparse
import json
import os
import secrets
import base64
import shlex
import subprocess
import sys
import uuid
import re
from typing import Dict, Iterable, List, Set

DEFAULT_MIN_PORT = 20000
DEFAULT_MAX_PORT = 60000
DEFAULT_DOCKER_IMAGE = "python:3.11-slim"
DOCKER_SENTINEL_ENV = "GENERATE_USER_IN_DOCKER"
YAML_SUFFIXES = (".yml", ".yaml")
USER_FILE_SUFFIXES = YAML_SUFFIXES + (".json",)
VALID_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
OPTIONS_WITH_VALUE = (
    "--users-dir",
    "--min-port",
    "--max-port",
    "--port",
    "--groups",
    "--hosts",
    "--docker-image",
)
USAGE_EXAMPLES = """示例:
  python3 generate_user.py add alice               # 创建 alice.yml，自动选端口
  python3 generate_user.py add bob --port 26000    # 指定端口
  python3 generate_user.py add carol --groups netflix --hosts ams,spt
  python3 generate_user.py delete bob              # 删除 bob.yml/.yaml/.json
  python3 generate_user.py list                    # 查看端口占用与文件路径
  python3 generate_user.py list --include-json     # 同时展示 json 文件（不展开数组）
  python3 generate_user.py list --details          # 展开 json 数组逐条显示，并自动包含 json
  python3 generate_user.py --docker add dave       # 在容器里执行，免安装 cryptography
"""


def validate_name(name: str) -> str:
    """限制用户名为字母、数字、下划线或短横线，避免路径注入。"""
    if not VALID_NAME_PATTERN.match(name):
        sys.stderr.write("用户名仅支持字母、数字、下划线和短横线。\n")
        sys.exit(1)
    return name


def iter_user_records(
    users_dir: str,
    verbose: bool = False,
    expand_lists: bool = True,
    allowed_suffixes: Iterable[str] = USER_FILE_SUFFIXES,
) -> Iterable[Dict[str, object]]:
    """遍历目录中的用户记录，兼容 dict 或 list 结构。

    expand_lists: True 时展开 json 数组逐条输出；False 时按文件汇总。
    """
    for fname in os.listdir(users_dir):
        if not fname.endswith(tuple(allowed_suffixes)):
            continue
        path = os.path.join(users_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            if verbose:
                sys.stderr.write(f"跳过无法解析的文件 {path}: {exc}\n")
            continue

        def build_record(obj, idx: int = None):
            if not isinstance(obj, dict):
                if verbose:
                    sys.stderr.write(f"跳过非对象条目 {path}\n")
                return None
            name_val = obj.get("name") if isinstance(obj.get("name"), str) else None
            port_val = obj.get("port") if isinstance(obj.get("port"), int) else None
            display_name = name_val or os.path.splitext(fname)[0]
            if idx is not None:
                display_name = f"{display_name}[{idx}]"
            return {"name": name_val or display_name, "port": port_val, "path": path, "display_name": display_name}

        if isinstance(data, dict):
            record = build_record(data)
            if record:
                yield record
        elif isinstance(data, list):
            records: List[Dict[str, object]] = []
            for idx, item in enumerate(data):
                record = build_record(item, idx if expand_lists else None)
                if record:
                    records.append(record)
            if expand_lists:
                for record in records:
                    yield record
            else:
                if not records:
                    continue
                names = [r.get("name") for r in records if isinstance(r.get("name"), str)]
                base_name = os.path.splitext(fname)[0]
                display_name = base_name
                if names:
                    unique_names = sorted(set(names))
                    display_name = unique_names[0] if len(unique_names) == 1 else f"{base_name} ({len(records)})"
                ports = {r.get("port") for r in records if isinstance(r.get("port"), int)}
                port_val = ports.pop() if len(ports) == 1 else None
                yield {
                    "name": display_name,
                    "port": port_val,
                    "path": path,
                    "display_name": display_name,
                }
        else:
            if verbose:
                sys.stderr.write(f"跳过无法识别的文件格式: {path}\n")


def load_ports(users_dir: str) -> Set[int]:
    """扫描 users 目录下现有端口，避免冲突。"""
    ports: Set[int] = set()
    for record in iter_user_records(users_dir, allowed_suffixes=USER_FILE_SUFFIXES):
        port = record.get("port")
        if isinstance(port, int):
            ports.add(port)
    return ports


def pick_port(existing_ports: Set[int], min_port: int, max_port: int) -> int:
    """选一个未占用的端口。"""
    candidates = [p for p in range(min_port, max_port + 1) if p not in existing_ports]
    if not candidates:
        raise RuntimeError("端口池耗尽，换个范围或清理占用端口。")
    return secrets.choice(candidates)


def generate_keys() -> Dict[str, str]:
    """生成 x25519 密钥对，使用 urlsafe base64 (去掉尾部=)。"""
    try:
        from cryptography.hazmat.primitives.asymmetric import x25519
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        sys.stderr.write(
            "cryptography 未安装。可以执行:\n"
            "  python3 -m ensurepip --default-pip && python3 -m pip install cryptography\n"
            "或使用系统包管理器安装，如: sudo apt install python3-cryptography\n"
            "如果不想在系统安装依赖，可使用 --docker 选项。\n"
        )
        sys.exit(1)

    priv = x25519.X25519PrivateKey.generate()
    pub = priv.public_key()

    priv_bytes = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_bytes = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    priv_b64 = base64.urlsafe_b64encode(priv_bytes).decode().rstrip("=")
    pub_b64 = base64.urlsafe_b64encode(pub_bytes).decode().rstrip("=")
    return {"private_key": priv_b64, "public_key": pub_b64}


def normalize_argv(raw_argv) -> list:
    """兼容旧用法: 若未指定 add/delete，则默认 add。"""
    argv = list(raw_argv)
    commands = {"add", "delete", "list"}
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in commands or token in ("-h", "--help"):
            return argv
        if token in OPTIONS_WITH_VALUE:
            i += 2
            continue
        if any(token.startswith(opt + "=") for opt in OPTIONS_WITH_VALUE):
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        # 第一个非选项参数视为 name，插入默认命令 add
        return argv[:i] + ["add"] + argv[i:]
    return argv


def strip_docker_flags(argv) -> list:
    """去掉 --docker/--docker-image，避免容器内递归触发。"""
    cleaned = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--docker":
            i += 1
            continue
        if token.startswith("--docker-image"):
            if token == "--docker-image" and i + 1 < len(argv):
                i += 2
            else:
                i += 1
            continue
        cleaned.append(token)
        i += 1
    return cleaned


def reexec_in_docker(args, forwarded_argv, need_crypto: bool) -> None:
    """在 Docker 容器内重新执行当前脚本。"""
    script_dir = os.path.abspath(os.path.dirname(__file__))
    users_dir = os.path.abspath(args.users_dir)

    mounts = [(script_dir, "/app")]
    try:
        common = os.path.commonpath([users_dir, script_dir])
    except ValueError:
        common = ""
    if os.path.isabs(args.users_dir) and common != script_dir:
        mounts.append((users_dir, users_dir))

    docker_cmd = ["docker", "run", "--rm"]
    for host, container in mounts:
        docker_cmd.extend(["-v", f"{host}:{container}"])
    docker_cmd.extend(
        ["-w", "/app", "-e", f"{DOCKER_SENTINEL_ENV}=1", args.docker_image]
    )

    inner_parts = []
    if need_crypto:
        inner_parts.append("python -m pip install --no-cache-dir cryptography")
    quoted_args = " ".join(shlex.quote(a) for a in forwarded_argv)
    inner_parts.append(f"python generate_user.py {quoted_args}")
    inner_cmd = " && ".join(inner_parts)

    docker_cmd.extend(["sh", "-c", inner_cmd])

    try:
        subprocess.run(docker_cmd, check=True)
    except FileNotFoundError:
        sys.stderr.write("未找到 docker，请安装 docker 或取消 --docker 选项。\n")
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(f"docker 执行失败，退出码 {exc.returncode}\n")
        sys.exit(exc.returncode)
    sys.exit(0)


def ensure_users_dir(users_dir: str) -> str:
    """确保用户目录存在，不存在则自动创建。"""
    abs_path = os.path.abspath(users_dir)
    if os.path.isdir(abs_path):
        return abs_path
    if os.path.exists(abs_path):
        sys.stderr.write(f"路径已存在但不是目录: {abs_path}\n")
        sys.exit(1)
    try:
        os.makedirs(abs_path, exist_ok=True)
        print(f"📁 已创建目录: {abs_path}")
    except OSError as exc:
        sys.stderr.write(f"无法创建目录 {abs_path}: {exc}\n")
        sys.exit(1)
    return abs_path


def add_user(args) -> None:
    """添加/覆盖单个用户配置。"""
    name = validate_name(args.name)
    users_dir = ensure_users_dir(args.users_dir)

    existing_ports = load_ports(users_dir)
    if args.port:
        if args.port in existing_ports:
            sys.stderr.write(f"端口 {args.port} 已被占用，换一个或调整范围。\n")
            sys.exit(1)
        port = args.port
    else:
        if args.min_port > args.max_port:
            sys.stderr.write("min_port 不能大于 max_port\n")
            sys.exit(1)
        port = pick_port(existing_ports, args.min_port, args.max_port)

    keys = generate_keys()
    record = {
        "name": name,
        "uuid": str(uuid.uuid4()),
        "port": port,
        "short_id": secrets.token_hex(8),
        "private_key": keys["private_key"],
        "public_key": keys["public_key"],
        "groups": [g.strip() for g in args.groups.split(",")] if args.groups else ["all"],
        "hosts": [h.strip() for h in args.hosts.split(",")] if args.hosts else ["all"],
    }

    out_path = os.path.join(users_dir, f"{name}.yml")
    if os.path.exists(out_path) and not args.force:
        sys.stderr.write(f"文件已存在: {out_path}，使用 --force 覆盖\n")
        sys.exit(1)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
        f.write("\n")

    print(f"✅ 已生成 {out_path}")
    for k in ("uuid", "port", "short_id", "private_key", "public_key"):
        print(f"{k}: {record[k]}")


def delete_user(args) -> None:
    """删除指定用户配置文件。"""
    name = validate_name(args.name)
    users_dir = ensure_users_dir(args.users_dir)
    candidates = [
        os.path.join(users_dir, f"{name}{ext}") for ext in (".yml", ".yaml", ".json")
    ]
    deleted = False
    for path in candidates:
        if os.path.exists(path):
            os.remove(path)
            print(f"🗑️ 已删除 {path}")
            deleted = True
    if deleted:
        return
    sys.stderr.write(f"未找到用户 {name} 对应的文件 (yml/yaml/json) 于 {users_dir}\n")
    sys.exit(1)


def list_users(args) -> None:
    """列出用户配置与端口。"""
    users_dir = ensure_users_dir(args.users_dir)
    include_json = args.include_json or args.details
    suffixes = USER_FILE_SUFFIXES if include_json else YAML_SUFFIXES
    records = list(
        iter_user_records(
            users_dir,
            verbose=True,
            expand_lists=args.details,
            allowed_suffixes=suffixes,
        )
    )
    if not records:
        print(f"{users_dir} 中未找到用户配置文件 (支持 .yml/.yaml/.json)。")
        return

    records.sort(key=lambda r: (str(r.get("name")), str(r.get("path"))))
    for record in records:
        port = record.get("port")
        port_display = str(port) if isinstance(port, int) else "-"
        rel_path = os.path.relpath(record.get("path"))
        print(f"{str(record.get('display_name')):<20} {port_display:<10} {rel_path}")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--users-dir", default="users", help="用户配置目录，默认 users (适用于 add/delete/list)"
    )
    common.add_argument(
        "--docker", action="store_true", help="在 Docker 容器内执行脚本，避免本机安装依赖"
    )
    common.add_argument(
        "--docker-image", default=DEFAULT_DOCKER_IMAGE, help="Docker 镜像，默认 python:3.11-slim"
    )

    parser = argparse.ArgumentParser(
        description="生成/删除 Reality 用户配置文件 (users/<name>.yml)",
        parents=[common],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=USAGE_EXAMPLES,
    )

    subparsers = parser.add_subparsers(dest="command")

    add_parser = subparsers.add_parser("add", parents=[common], help="添加或覆盖用户配置")
    add_parser.add_argument("name", help="用户名 (文件名将为 name.yml)")
    add_parser.add_argument("--port", type=int, help="可选：指定端口；默认自动选择未占用的")
    add_parser.add_argument(
        "--groups",
        type=str,
        default="all",
        help="允许访问的节点组，逗号分隔，例如 premium,netflix。默认为 all",
    )
    add_parser.add_argument(
        "--hosts",
        type=str,
        default="all",
        help="允许访问的具体节点，逗号分隔，例如 ams,dcc。默认为 all",
    )
    add_parser.add_argument(
        "--min-port", type=int, default=DEFAULT_MIN_PORT, help="自动分配端口下限，默认 20000"
    )
    add_parser.add_argument(
        "--max-port", type=int, default=DEFAULT_MAX_PORT, help="自动分配端口上限，默认 60000"
    )
    add_parser.add_argument("--force", action="store_true", help="如文件已存在则覆盖")

    delete_parser = subparsers.add_parser("delete", parents=[common], help="删除用户配置文件")
    delete_parser.add_argument("name", help="用户名 (删除 name.yml/.yaml/.json)")

    list_parser = subparsers.add_parser("list", parents=[common], help="列出已有用户与端口")
    list_parser.add_argument(
        "--details", action="store_true", help="展开 json 数组逐条显示（自动包含 json 文件）"
    )
    list_parser.add_argument(
        "--include-json",
        action="store_true",
        help="默认只显示 yml/yaml；使用该选项显示 json（不展开数组）",
    )

    return parser


def main():
    raw_argv = sys.argv[1:]
    argv = normalize_argv(raw_argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return

    if args.command in ("add", "delete", "list") and os.environ.get(DOCKER_SENTINEL_ENV) != "1":
        ensure_users_dir(args.users_dir)

    forwarded_argv = strip_docker_flags(argv)
    need_crypto = args.command == "add"
    if args.docker and os.environ.get(DOCKER_SENTINEL_ENV) != "1":
        reexec_in_docker(args, forwarded_argv, need_crypto)

    if args.command == "delete":
        delete_user(args)
    elif args.command == "list":
        list_users(args)
    else:
        add_user(args)


if __name__ == "__main__":
    main()
