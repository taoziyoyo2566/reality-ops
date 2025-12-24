eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
# 这里输入一次密码，之后当前会话中 Ansible 连接所有服务器都不需要再输密码



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