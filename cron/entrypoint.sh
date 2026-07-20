#!/bin/sh
set -eu

# API_URL y CRON_SCHEDULE son configurables por env var (distintos entre dev/prod);
# CRON_SECRET se "hornea" en el crontab acá porque crond no hereda el entorno del
# contenedor al ejecutar los jobs.
: "${API_URL:=http://fastapi:8000}"
: "${CRON_SCHEDULE:=5 0 * * *}"

if [ -z "${CRON_SECRET:-}" ]; then
  echo "ERROR: falta CRON_SECRET en el entorno del contenedor cron" >&2
  exit 1
fi

ENDPOINT="${API_URL}/api/facturacion/periodo-medico/cerrar-vencidos"

cat > /etc/crontabs/root <<EOF
${CRON_SCHEDULE} curl -fsS -X POST -H "X-Cron-Secret: ${CRON_SECRET}" "${ENDPOINT}" >> /var/log/cron.log 2>&1
EOF

echo "cron configurado: '${CRON_SCHEDULE}' -> ${ENDPOINT}"
touch /var/log/cron.log

exec crond -f -l 2
