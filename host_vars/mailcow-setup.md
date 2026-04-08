## mailcow 

### requirement
1, clean, 
2, mailcow.conf check(ip, docker ip)
3, cloudflare FP
4, custome ipv6
curl -6 ifconfig.co
2604:9cc0:1c72::58f2:83da:4b0e:e17c
5, bind custome ipv6 to eth0
sudo ip -6 addr del 2604:9cc0:1c72::58f2:83da:4b0e:e17c/48 dev eth0

6, rDNS
7, hostname
mail.taoziyoyo.com
8,  hosts
127.0.0.1   mail.taoziyoyo.com mail
2604:9cc0:1c72::58f2:83da:4b0e:e17c     mail.taoziyoyo.com mail

9, active hostname
sudo hostnamectl set-hostname mail.taoziyoyo.com
hostname -f

