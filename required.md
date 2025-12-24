eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_rsa_encrypted
# 这里输入一次密码，之后当前会话中 Ansible 连接所有服务器都不需要再输密码