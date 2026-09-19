# 节点时钟同步：检查与安装 systemd-timesyncd

节点自己打的时间戳（上报的统计周期、控制台显示的 Xray 重启时间）来自节点时钟；时钟偏了，这些时间跟着偏。
新系统节点的 REALITY 没有设置 `maxTimeDiff`，用户连接不受节点时钟影响。

除特别注明外，命令都在 `spt` 的仓库根目录执行，需要已载入节点密钥的 ssh-agent：

```bash
cd ~/workspace/projects/reality-ops
export SSH_AUTH_SOCK=/run/user/1000/openssh_agent
```

## 1. 现状

| 节点 | 校时方式 |
|---|---|
| kagoya | chrony |
| ams、dcc、dzire、hk01、hk02、jp05、jp10、legend、netcup、usca | systemd-timesyncd（Debian 默认的 `debian.pool.ntp.org`） |

新节点加入 `edge_nodes` 前按第 2 节检查一次。

## 2. 检查（只读）

与 spt 的时钟差和是否已同步（差值含 SSH 往返的误差，1–2 秒以内正常）：

```bash
for h in ams dcc dzire hk01 hk02 jp05 jp10 kagoya legend netcup usca; do
  a=$(date +%s.%N)
  r=$(ssh -o BatchMode=yes $h 'date +%s.%N; timedatectl show -p NTPSynchronized --value')
  b=$(date +%s.%N)
  python3 -c "a, b = $a, $b; t, s = '''$r'''.split(); print('%-7s %+7.1fs synced=%s' % ('$h', float(t) - (a + b) / 2, s))"
done
```

`synced=no` 或差值超过几秒时，在该节点上确认：

```bash
ssh <节点> 'systemd-detect-virt
  dpkg-query -W -f="\${Package} \${Status}\n" chrony ntp ntpsec openntpd systemd-timesyncd 2>/dev/null | grep "ok installed"
  sudo apt-get install -s --no-install-recommends --no-remove systemd-timesyncd | grep -E "^(Inst|Remv)|newly installed"'
```

- `systemd-detect-virt` 为 `kvm`、`xen`、`microsoft` 或 `none`（物理机）时可以在机器上校时；为 `openvz` 或 `lxc` 时时钟跟随宿主机，
  机器上改不了，找服务商处理。
- 已经装有 chrony、ntp、ntpsec 或 openntpd 之一时**不要**再装 systemd-timesyncd：这些包互相冲突，apt 会删掉原来的那个。
  改为检查原来那个服务为什么没有同步（`systemctl status <服务>`）。
- 模拟安装的结果必须是 `0 upgraded, 1 newly installed, 0 to remove`，并且只有 `Inst systemd-timesyncd` 一行。
  如果要求升级 systemd 或删除别的包，先停下，另行决定。

## 3. 安装

一次一台，装好并核对后再装下一台：

```bash
ssh <节点> 'sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends --no-remove systemd-timesyncd
  systemctl is-enabled systemd-timesyncd; systemctl is-active systemd-timesyncd'
```

- 安装后服务自动启用并开始同步，时钟一次性调整到正确时间（慢了多少就往前跳多少）。Docker 容器与主机共用时钟，
  Xray、agent 与旧系统的容器都不需要重启。
- 同步使用 UDP 123 出站访问 Debian 的时间服务器池。

## 4. 核对

```bash
ssh <节点> 'timedatectl show -p NTPSynchronized --value
  sudo journalctl -u systemd-timesyncd --no-pager -o cat | grep -E "Contacted|Initial clock" | tail -2
  sudo docker ps --format "{{.Names}} {{.Status}}"'
```

- 第一行应为 `yes`，日志中有 `Contacted time server …` 与 `Initial clock synchronization …`，容器都在运行。
- 再按第 2 节量一次时钟差。
- `timedatectl show-timesync` 在这些节点上会一直等待不返回，查看所用服务器用上面的日志。

## 5. 回退

```bash
ssh <节点> 'sudo apt-get remove -y systemd-timesyncd'
```

时钟停在当时的值，之后又会慢慢漂移。
