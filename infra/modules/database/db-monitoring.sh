#!/bin/bash
# Put the database server's monitoring in place and keep it that way (KAV-82, ADR-0050).
#
# Run as root ON the database server, by an SSM State Manager association (monitoring.tf), not by
# first-boot user_data: `ignore_changes = [user_data]` means an edited boot script never reaches a
# server that already exists (production's), while an association reaches every server tagged for
# it, new or old, when it is created, on a daily schedule, and whenever a stopped server comes back
# online. The script is idempotent: running it again changes nothing that is already right.
#
# What it sets up:
#   1. a Postgres role `kaval_monitor` that can only read statistics (the built-in pg_monitor
#      role: no table data, no writes), with its own generated password from Parameter Store;
#   2. two small exporters, each in a container capped at 64 MiB, listening on the private address
#      only (the security group lets in the app server and nobody else):
#        node_exporter      :9100  disk space, memory, load (filesystem, meminfo, loadavg, cpu)
#        postgres_exporter  :9187  Postgres' own statistics, as kaval_monitor over TLS
#   3. connection and failed-login logging in Postgres (ADR-0008 asked for it), and log rotation for
#      the Docker daemon so that logging cannot fill the disk it is meant to protect.
#
# Expects NAME_PREFIX, NODE_EXPORTER_IMAGE and POSTGRES_EXPORTER_IMAGE in the environment (the
# association's document puts them on the first lines). Prints what it changed and never a secret.
set -euo pipefail

: "${NAME_PREFIX:?}" "${NODE_EXPORTER_IMAGE:?}" "${POSTGRES_EXPORTER_IMAGE:?}"

TOKEN=$(curl -fsS -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 300')
imds() { curl -fsS -H "X-aws-ec2-metadata-token: $TOKEN" "http://169.254.169.254/latest/meta-data/$1"; }
REGION=$(imds placement/region)
IP=$(imds local-ipv4)
changed=0

# ── wait for Postgres: this can run the moment the server comes online, before it is ready ──
for _ in $(seq 1 60); do
  docker exec postgres pg_isready -U kaval -d kaval > /dev/null 2>&1 && break
  sleep 3
done
docker exec postgres pg_isready -U kaval -d kaval > /dev/null 2>&1 \
  || { echo "postgres is not answering; will retry at the next run"; exit 1; }

# ── 1. the monitoring role, and logging ──────────────────────────────────────────────────────
PW=$(aws ssm get-parameter --region "$REGION" --name "/kaval/${NAME_PREFIX}/db/monitor-password" \
  --with-decryption --query Parameter.Value --output text)
# The password reaches psql through the environment, not the command line (a command line is
# visible to every process listing). \getenv is psql 15+; the image is Postgres 16.
docker exec -i -e MONITOR_PW="$PW" postgres psql -U kaval -d kaval -v ON_ERROR_STOP=1 -q <<'SQL'
\getenv pw MONITOR_PW
SELECT format('CREATE ROLE kaval_monitor LOGIN PASSWORD %L', :'pw')
  WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'kaval_monitor') \gexec
SELECT format('ALTER ROLE kaval_monitor LOGIN PASSWORD %L', :'pw') \gexec
GRANT pg_monitor TO kaval_monitor;
-- Who connected, from where, and who failed to: written to the Postgres log. A failed password is
-- logged by Postgres anyway ("password authentication failed for user"); the prefix adds the
-- time, the process, the user, the database and the client address to every line.
ALTER SYSTEM SET log_connections = 'on';
ALTER SYSTEM SET log_disconnections = 'on';
ALTER SYSTEM SET log_line_prefix = '%m [%p] %u@%d %h ';
SELECT pg_reload_conf();
SQL

# ── 2. the exporters ─────────────────────────────────────────────────────────────────────────
# The exporter's connection settings: root-only file, never on a command line.
umask 077
printf 'DATA_SOURCE_NAME=postgresql://kaval_monitor:%s@host.docker.internal:5432/kaval?sslmode=require\n' "$PW" > /etc/kaval-postgres-exporter.env
chmod 600 /etc/kaval-postgres-exporter.env
umask 022
unset PW

write_unit() {  # name, contents on stdin: replace only when different
  local path="/etc/systemd/system/$1.service" new
  new=$(cat)
  if [ ! -f "$path" ] || [ "$(cat "$path")" != "$new" ]; then
    printf '%s\n' "$new" > "$path"
    changed=1
    echo "wrote $1.service"
  fi
}

write_unit kaval-node-exporter <<UNIT
[Unit]
Description=Kaval database server: machine monitor (node_exporter)
After=docker.service
Requires=docker.service

[Service]
ExecStartPre=-/usr/bin/docker rm -f node-exporter
ExecStart=/usr/bin/docker run --rm --name node-exporter --memory 64m \\
  --log-opt max-size=5m --log-opt max-file=2 \\
  -p ${IP}:9100:9100 -v /:/host:ro,rslave \\
  ${NODE_EXPORTER_IMAGE} --path.rootfs=/host \\
  --collector.disable-defaults --collector.filesystem --collector.meminfo \\
  --collector.loadavg --collector.cpu
ExecStop=/usr/bin/docker stop node-exporter
TimeoutStartSec=300
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

write_unit kaval-postgres-exporter <<UNIT
[Unit]
Description=Kaval database server: Postgres monitor (postgres_exporter)
After=docker.service kaval-postgres.service
Requires=docker.service

[Service]
ExecStartPre=-/usr/bin/docker rm -f postgres-exporter
ExecStart=/usr/bin/docker run --rm --name postgres-exporter --memory 64m \\
  --log-opt max-size=5m --log-opt max-file=2 \\
  --add-host=host.docker.internal:host-gateway --env-file /etc/kaval-postgres-exporter.env \\
  -p ${IP}:9187:9187 ${POSTGRES_EXPORTER_IMAGE}
ExecStop=/usr/bin/docker stop postgres-exporter
TimeoutStartSec=300
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

if [ "$changed" = 1 ]; then systemctl daemon-reload; fi
for unit in kaval-node-exporter kaval-postgres-exporter; do
  systemctl enable "$unit" > /dev/null 2>&1
  if [ "$changed" = 1 ]; then systemctl restart "$unit"; else systemctl start "$unit"; fi
done

# ── 3. log rotation for Docker's own default ─────────────────────────────────────────────────
# Postgres runs in a container whose log Docker keeps without limit by default, and connection
# logging makes it longer. Set a default cap for new containers. The daemon is NOT restarted here
# (that would stop Postgres unclean); it takes effect the next time the daemon starts, which the
# nightly stop/start does.
if ! jq -e '."log-opts"."max-size"' /etc/docker/daemon.json > /dev/null 2>&1; then
  tmp=$(mktemp)
  if [ -s /etc/docker/daemon.json ]; then
    jq '. + {"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}' /etc/docker/daemon.json > "$tmp"
  else
    echo '{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}' > "$tmp"
  fi
  install -m 644 "$tmp" /etc/docker/daemon.json
  rm -f "$tmp"
  echo "docker log rotation configured (applies when the daemon next starts)"
fi

# ── done: say what is true now ───────────────────────────────────────────────────────────────
for _ in $(seq 1 20); do
  curl -fsS "http://${IP}:9100/metrics" > /dev/null 2>&1 && curl -fsS "http://${IP}:9187/metrics" > /dev/null 2>&1 && break
  sleep 3
done
curl -fsS "http://${IP}:9100/metrics" > /dev/null && echo "node_exporter answering on ${IP}:9100"
curl -fsS "http://${IP}:9187/metrics" | grep -E '^pg_up ' || { echo "postgres_exporter is not reporting pg_up" >&2; exit 1; }
