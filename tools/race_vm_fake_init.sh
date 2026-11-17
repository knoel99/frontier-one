#!/bin/bash
# First-boot script of a fake-race VM (cloud-init user data), filled in by `tools/race_vm.py up --fake`: never run by
# hand. No key reaches this VM and no model is called: tools/fake_race.py writes a synthetic race (__FAKEARGS__)
# and keeps it moving, and the dashboard serves it over HTTPS at <ip>.sslip.io behind its token.
set -eu
export HOME=/root DEBIAN_FRONTEND=noninteractive
exec >>/var/log/race-vm.log 2>&1
echo "== boot $(date -u +%FT%TZ)"
report() { tail -c 60000 /var/log/race-vm.log | curl -s --local-port 1-1023 -X PATCH -H 'Content-Type: text/plain' \
  --data-binary @- http://169.254.42.42/user_data/race-log >/dev/null || true; }
trap 'echo "== FAILED at line $LINENO: $BASH_COMMAND"; report' ERR

apt-get update -q && apt-get install -y -q git python3 curl ca-certificates
arch=$(dpkg --print-architecture)
curl -fsSL "https://github.com/caddyserver/caddy/releases/download/v2.11.9/caddy_2.11.9_linux_${arch}.tar.gz" \
  | tar -xz -C /usr/local/bin caddy

useradd -m -s /bin/bash frontier
F=/home/frontier
git clone -q --branch '__BRANCH__' https://github.com/knoel99/frontier-one $F/frontier-one
mkdir -p $F/race && printf 'FRONTIER_DASHBOARD_TOKEN=__TOKEN__\n' > $F/race/dashboard.env
chmod 600 $F/race/dashboard.env && chown -R frontier: $F
mkdir -p /etc/caddy && printf '__HOST__ {\n\treverse_proxy 127.0.0.1:8800\n}\n' > /etc/caddy/Caddyfile

unit() {  # name, user, command
  printf '[Unit]\nAfter=network-online.target\n[Service]\nUser=%s\nWorkingDirectory=%s/frontier-one\nEnvironmentFile=%s/race/dashboard.env\nExecStart=%s\nRestart=on-failure\nStandardOutput=append:/var/log/%s.log\nStandardError=append:/var/log/%s.log\n[Install]\nWantedBy=multi-user.target\n' \
    "$2" "$F" "$F" "$3" "$1" "$1" > "/etc/systemd/system/$1.service"
}
unit race-fake frontier "/usr/bin/python3 tools/fake_race.py $F/race/missions __FAKEARGS__"
unit race-dashboard frontier "/usr/bin/python3 -m frontier dashboard --root $F/race/missions --port 8800"
unit race-caddy root "/usr/local/bin/caddy run --config /etc/caddy/Caddyfile"
touch /var/log/race-fake.log /var/log/race-dashboard.log && chown frontier: /var/log/race-fake.log /var/log/race-dashboard.log
systemctl daemon-reload && systemctl enable --now race-fake
# the generator writes the whole period before it goes live: the dashboard starts once the data is complete
for i in $(seq 300); do grep -q '^live:' /var/log/race-fake.log && break; sleep 2; done
grep -q '^live:' /var/log/race-fake.log || { echo '== the generator never went live'; tail -n 40 /var/log/race-fake.log;
  systemctl status race-fake --no-pager | tail -n 5; report; exit 1; }
systemctl enable --now race-caddy race-dashboard
# the certificate comes from Let's Encrypt within a minute or two; the report says whether it came, with Caddy's log
for i in $(seq 60); do [ "$(curl -s -o /dev/null -m 5 -w '%{http_code}' https://__HOST__/)" != 000 ] && break; sleep 5; done
echo "== HTTPS: $(curl -s -o /dev/null -m 5 -w '%{http_code}' https://__HOST__/) (403 without the token is the expected answer)"
tail -n 15 /var/log/race-caddy.log || true
echo "== ready https://__HOST__/ $(date -u +%FT%TZ)"
report
