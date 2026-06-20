#!/usr/bin/env bash
set -euo pipefail

echo "== Tiny Debian 12 cleanup: keep existing Docker + ufw + fail2ban =="

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0"
  exit 1
fi

echo
echo "== Before =="
df -h /
du -h --max-depth=1 / 2>/dev/null | sort -h || true

echo
echo "== APT future behavior: no recommends/suggests/language indexes =="
cat >/etc/apt/apt.conf.d/99lean-vps <<'EOF'
APT::Install-Recommends "false";
APT::Install-Suggests "false";
Acquire::Languages "none";
EOF

echo
echo "== dpkg future behavior: do not install docs/man/info/most locales =="
cat >/etc/dpkg/dpkg.cfg.d/01lean-vps <<'EOF'
path-exclude=/usr/share/doc/*
path-exclude=/usr/share/man/*
path-exclude=/usr/share/info/*
path-exclude=/usr/share/lintian/*
path-exclude=/usr/share/linda/*
path-exclude=/usr/share/locale/*
path-include=/usr/share/locale/locale.alias
path-include=/usr/share/locale/en*
path-include=/usr/share/locale/C*
EOF

echo
echo "== Purge clearly unnecessary packages if present =="
PURGE_CANDIDATES=(
  man-db
  manpages
  manpages-dev
  info
  install-info
  doc-debian
  debian-faq
  vim
  vim-common
  vim-runtime
  emacs
  exim4
  exim4-base
  exim4-config
  exim4-daemon-light
  bsd-mailx
  popularity-contest
  reportbug
  tasksel
  tasksel-data
  dictionaries-common
  wamerican
  iamerican
  aspell
  aspell-en
  ispell
  krb5-locales
  laptop-detect
  wireless-regdb
  iw
  wpasupplicant
  firmware-linux-free
)

INSTALLED=()
for pkg in "${PURGE_CANDIDATES[@]}"; do
  if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
    INSTALLED+=("$pkg")
  fi
done

if (( ${#INSTALLED[@]} > 0 )); then
  apt-get purge -y "${INSTALLED[@]}"
else
  echo "No purge candidates installed."
fi

echo
echo "== Autoremove orphaned dependencies =="
apt-get autoremove --purge -y

echo
echo "== Clean APT cache and package lists =="
apt-get clean
apt-get autoclean
rm -rf /var/lib/apt/lists/*

echo
echo "== Remove docs/man/info/unused locales already on disk =="
rm -rf /usr/share/doc/*
rm -rf /usr/share/man/*
rm -rf /usr/share/info/*
rm -rf /usr/share/lintian/*
rm -rf /usr/share/linda/*

if [[ -d /usr/share/locale ]]; then
  find /usr/share/locale -mindepth 1 -maxdepth 1 \
    ! -name 'locale.alias' \
    ! -name 'en*' \
    ! -name 'C*' \
    -exec rm -rf {} + 2>/dev/null || true
fi

echo
echo "== Trim logs =="
journalctl --rotate || true
journalctl --vacuum-size=16M || true

find /var/log -type f -name "*.gz" -delete 2>/dev/null || true
find /var/log -type f -name "*.1" -delete 2>/dev/null || true
find /var/log -type f -name "*.old" -delete 2>/dev/null || true
find /var/log -type f -exec truncate -s 0 {} \; 2>/dev/null || true

echo
echo "== Limit future journald size =="
mkdir -p /etc/systemd/journald.conf.d
cat >/etc/systemd/journald.conf.d/99-lean-vps.conf <<'EOF'
[Journal]
SystemMaxUse=16M
RuntimeMaxUse=8M
MaxRetentionSec=3day
Compress=yes
EOF
systemctl restart systemd-journald || true

echo
echo "== Limit Docker JSON logs =="
if command -v docker >/dev/null 2>&1; then
  mkdir -p /etc/docker

  if [[ -f /etc/docker/daemon.json ]]; then
    cp -a /etc/docker/daemon.json "/etc/docker/daemon.json.bak.$(date +%s)"
  fi

  cat >/etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "5m",
    "max-file": "2"
  }
}
EOF

  systemctl restart docker || true
else
  echo "Docker command not found, skip Docker log config."
fi

echo
echo "== Safely prune unused Docker data =="
if command -v docker >/dev/null 2>&1; then
  docker system df || true

  docker container prune -f || true
  docker image prune -f || true
  docker builder prune -f || true
  docker network prune -f || true

  docker system df || true
fi

echo
echo "== Remove temp/cache files =="
rm -rf /tmp/* /var/tmp/* 2>/dev/null || true
rm -rf /root/.cache /home/*/.cache 2>/dev/null || true

echo
echo "== Remove Python bytecode caches =="
find /usr /root /home -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find /usr /root /home -type f -name '*.pyc' -delete 2>/dev/null || true

echo
echo "== Verify critical commands =="
for cmd in sshd python3 sudo docker ufw fail2ban-client; do
  if command -v "$cmd" >/dev/null 2>&1; then
    echo "OK: $cmd"
  else
    echo "WARN: missing $cmd"
  fi
done

echo
echo "== After =="
df -h /
du -h --max-depth=1 / 2>/dev/null | sort -h || true

echo
echo "Done."
