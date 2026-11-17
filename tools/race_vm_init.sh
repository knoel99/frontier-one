#!/bin/bash
# First-boot script of a race VM (cloud-init user data), filled in by tools/race_vm.py: never run by hand.
# As root: system packages, Caddy (HTTPS for <ip>.sslip.io), a non-root user `frontier`, the framework, the keys from
# Scaleway Secret Manager; then, as `frontier`, the mission's harness, the mission and its dashboard, as services.
set -eu
export HOME=/root DEBIAN_FRONTEND=noninteractive
exec >>/var/log/race-vm.log 2>&1
echo "== boot $(date -u +%FT%TZ)"
# The log's tail goes to this server's own user data (key race-log), read by `tools/race_vm.py log`: no SSH needed.
report() { tail -c 60000 /var/log/race-vm.log | curl -s --local-port 1-1023 -X PATCH -H 'Content-Type: text/plain' \
  --data-binary @- http://169.254.42.42/user_data/race-log >/dev/null || true; }
trap 'echo "== FAILED at line $LINENO: $BASH_COMMAND"; report' ERR

apt-get update -q && apt-get install -y -q git python3 curl ca-certificates xz-utils python3-sympy python3-pip \
  pandoc texlive-latex-recommended texlive-fonts-recommended lmodern  # the snapshots' PDF report (frontier/snapshots.py)
# the framework's optional modules (pyproject.toml: math, publish), for the system python3 the services run
pip install -q --break-system-packages --ignore-installed "huggingface_hub>=1.0"  # beside Debian's own packages
# Ubuntu 24.04 denies user namespaces to non-root users; the harnesses' own sandboxes (Codex: bwrap) need them
echo kernel.apparmor_restrict_unprivileged_userns=0 > /etc/sysctl.d/60-race-userns.conf
sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y -q nodejs
arch=$(dpkg --print-architecture)
curl -fsSL "https://github.com/caddyserver/caddy/releases/download/v2.11.9/caddy_2.11.9_linux_${arch}.tar.gz" \
  | tar -xz -C /usr/local/bin caddy

useradd -m -s /bin/bash frontier
chown frontier: /opt  # recipes that build into /opt (zcode)
F=/home/frontier
git clone -q --branch '__BRANCH__' https://github.com/knoel99/frontier-one $F/frontier-one
mkdir -p $F/race && echo '__MISSION_B64__' | base64 -d > $F/race/mission.toml
[ -n '__SUBJECT_NAME__' ] && echo '__SUBJECT_B64__' | base64 -d > "$F/race/__SUBJECT_NAME__"

# The keys: read once with the VM's own Scaleway key, kept in a file only `frontier` can read.
SCW_SECRET_KEY='__SCW_KEY__' python3 - > $F/race/keys.env <<'PY'
import base64, json, os, urllib.request
req = urllib.request.Request(
    "https://api.scaleway.com/secret-manager/v1beta1/regions/__REGION__/secrets-by-path/versions/latest/access"
    "?project_id=__PROJECT__&secret_path=/&secret_name=__SECRET__", headers={"X-Auth-Token": os.environ["SCW_SECRET_KEY"]})
keys = json.loads(base64.b64decode(json.load(urllib.request.urlopen(req, timeout=60))["data"]))
for k, v in {**keys, "SCW_SECRET_KEY": os.environ["SCW_SECRET_KEY"]}.items():
    if v and "\n" not in v:
        print(f"{k}={v}")
print("SCW_DEFAULT_PROJECT_ID=__PROJECT__\nSCW_DEFAULT_ZONE=__ZONE__\nFRONTIER_DASHBOARD_TOKEN=__TOKEN__")
PY
cat >> $F/race/keys.env <<EOF
HOME=$F
NPM_CONFIG_PREFIX=$F/.npm-global
PATH=$F/.npm-global/bin:$F/.local/bin:/opt/node24/bin:/usr/local/bin:/usr/bin:/bin
EOF
chmod 600 $F/race/keys.env && chown -R frontier: $F

# The mission's harness, installed by its own recipe, as `frontier`.
sudo -u frontier -H bash -c "set -a; . $F/race/keys.env; set +a; curl -LsSf https://astral.sh/uv/install.sh | sh; cd $F/frontier-one && \
  install=\$(python3 -c 'import sys; from frontier import mission, launcher; m = mission.load(sys.argv[1]); \
print(launcher.harness_conf(m[\"harness\"][\"name\"], m[\"llm\"][\"provider\"]).get(\"install\", \"\"))' $F/race/mission.toml) && \
  echo \"== install: \$install\" && bash -c \"\$install\"" || echo "== harness install failed"

mkdir -p /etc/caddy && printf '__HOST__ {\n\treverse_proxy 127.0.0.1:8800\n}\n' > /etc/caddy/Caddyfile

unit() {  # name, user, command
  printf '[Unit]\nAfter=network-online.target\n[Service]\nUser=%s\nWorkingDirectory=%s/frontier-one\nEnvironmentFile=%s/race/keys.env\nExecStart=%s\nStandardOutput=append:/var/log/%s.log\nStandardError=append:/var/log/%s.log\n[Install]\nWantedBy=multi-user.target\n' \
    "$2" "$F" "$F" "$3" "$1" "$1" > "/etc/systemd/system/$1.service"
}
unit race-dashboard frontier "/usr/bin/python3 -m frontier dashboard --port 8800"
unit race-mission frontier "/usr/bin/python3 -m frontier mission start $F/race/mission.toml"
unit race-caddy root "/usr/local/bin/caddy run --config /etc/caddy/Caddyfile"
touch /var/log/race-dashboard.log /var/log/race-mission.log && chown frontier: /var/log/race-dashboard.log /var/log/race-mission.log
systemctl daemon-reload && systemctl enable --now race-caddy race-dashboard race-mission
# the certificate comes from Let's Encrypt within a minute or two; the report says whether it came, with Caddy's log
for i in $(seq 60); do [ "$(curl -s -o /dev/null -m 5 -w '%{http_code}' https://__HOST__/)" != 000 ] && break; sleep 5; done
echo "== HTTPS: $(curl -s -o /dev/null -m 5 -w '%{http_code}' https://__HOST__/) (403 without the token is the expected answer)"
tail -n 15 /var/log/race-caddy.log || true
echo "== ready https://__HOST__/ $(date -u +%FT%TZ)"
report
