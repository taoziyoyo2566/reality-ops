eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
# 这里输入一次密码，之后当前会话中 Ansible 连接所有服务器都不需要再输密码

对于非1000的用户，需要手动sudo文件
```
sudo visudo
kagoya ALL=(ALL) NOPASSWD: ALL
```
spartan@taoziyoyo:~/workspace/reality-ops$ id
uid=1000(spartan) gid=1000(spartan) groups=1000(spartan),27(sudo),990(docker)

ansible -i inventory.ini all -m ping
dzire | SUCCESS => {
    "ansible_facts": {
        "discovered_interpreter_python": "/usr/bin/python3.11"
    },
    "changed": false,
    "ping": "pong"
}

ansible-playbook -i inventory.ini deploy.yml --check --diff

ansible-playbook -i inventory.ini deploy.yml

# 提取所有 .yml 和 .json 文件中的端口号
grep -hEo '"?port"?: ?[0-9]+' users/*.yml users/*.json | grep -oE '[0-9]+'


# allow tcp
grep -hEo '"?port"?: ?[0-9]+' users/*.yml users/*.json \
| grep -oE '[0-9]+' \
| sort -u \
| xargs -I{} sudo ufw allow {}\/tcp

# udp & tcp
xargs -I{} sh -c 'sudo ufw allow {}/tcp && sudo ufw allow {}/udp'

# 查看 xray 进程监听的 TCP 端口
sudo ss -tulpn | grep xray

# grep -v
grep -hEo '"?port"?: ?[0-9]+' users/*.yml users/*.json \
| grep -oE '[0-9]+' \
| grep -v '^10085$' \
| sort -u \
| xargs -I{} sudo ufw allow {}\/tcp

# awk
grep -hEo '"?port"?: ?[0-9]+' users/*.yml users/*.json \
| grep -oE '[0-9]+' \
| awk '$1 != 10085' \
| sort -u \
| xargs -I{} sudo ufw allow {}\/tcp

#
docker ps -a \
  --filter name=reality_ \
  --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}'

#
docker ps -a \
  --filter name=reality_ \
  --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}' \
| grep -Ev 'reality_tom_TIVGQQAF|reality_zhi_TG736HNZ|reality_starry_3IUIXRGY'

docker ps -a \
  --filter name=reality_ \
  --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}' \
| grep -Ev 'reality_tom_TIVGQQAF|reality_zhi_TG736HNZ|reality_starry_3IUIXRGY' \
| awk '{print $1}' \
| xargs docker stop

docker ps -a \
  --filter name=reality_ \
  --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}' \
| grep -Ev 'reality_tom_TIVGQQAF|reality_zhi_TG736HNZ|reality_starry_3IUIXRGY' \
| awk '{print $1}' \
| xargs docker rm