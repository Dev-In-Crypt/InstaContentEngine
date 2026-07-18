#!/bin/sh
# Daily Postgres backup loop for the self-hosted deploy. Run by the `backup`
# service in docker-compose. Dumps the DB gzipped into /backups and prunes dumps
# older than BACKUP_KEEP_DAYS. PGPASSWORD is passed in by compose.
set -eu

KEEP="${BACKUP_KEEP_DAYS:-7}"
DB_HOST="${DB_HOST:-db}"
DB_USER="${DB_USER:-insta}"
DB_NAME="${DB_NAME:-insta}"

mkdir -p /backups
echo "[backup] started — dumping ${DB_NAME} daily, keeping ${KEEP} days"

while true; do
  ts="$(date +%Y%m%d_%H%M%S)"
  out="/backups/${DB_NAME}_${ts}.sql.gz"
  echo "[backup] $(date -u) dumping to ${out}"
  if pg_dump -h "${DB_HOST}" -U "${DB_USER}" "${DB_NAME}" | gzip > "${out}"; then
    echo "[backup] ok: ${out}"
  else
    echo "[backup] FAILED — removing partial file" >&2
    rm -f "${out}"
  fi
  # Prune dumps older than KEEP days.
  find /backups -name "${DB_NAME}_*.sql.gz" -mtime +"${KEEP}" -delete 2>/dev/null || true
  sleep 86400
done
