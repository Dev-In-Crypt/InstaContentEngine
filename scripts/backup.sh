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
  # --clean --if-exists so restoring the dump drops+recreates objects instead of
  # colliding with tables the app already made via create_all.
  if pg_dump --clean --if-exists -h "${DB_HOST}" -U "${DB_USER}" "${DB_NAME}" | gzip > "${out}"; then
    echo "[backup] ok: ${out}"
  else
    echo "[backup] FAILED — removing partial file" >&2
    rm -f "${out}"
  fi
  # Archive uploaded media (mounted read-only at /uploads) so a restore isn't
  # left with posts whose images are gone.
  if [ -d /uploads ]; then
    up="/backups/uploads_${ts}.tgz"
    if tar czf "${up}" -C /uploads . 2>/dev/null; then
      echo "[backup] ok: ${up}"
    else
      echo "[backup] uploads archive FAILED" >&2
      rm -f "${up}"
    fi
  fi
  # Prune dumps + uploads archives older than KEEP days.
  find /backups -name "${DB_NAME}_*.sql.gz" -mtime +"${KEEP}" -delete 2>/dev/null || true
  find /backups -name "uploads_*.tgz" -mtime +"${KEEP}" -delete 2>/dev/null || true
  sleep 86400
done
