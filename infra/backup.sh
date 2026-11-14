#!/bin/sh
set -eu

interval="${BACKUP_INTERVAL_SECONDS:-3600}"
retention="${BACKUP_RETENTION_DAYS:-7}"
backup_dir="${BACKUP_DIRECTORY:-/backups}"

case "$interval" in ''|*[!0-9]*) exit 64 ;; esac
case "$retention" in ''|*[!0-9]*) exit 64 ;; esac
[ "$interval" -gt 0 ] && [ "$retention" -gt 0 ] || exit 64

umask 077
mkdir -p "$backup_dir"
partial=''
trap '[ -z "$partial" ] || rm -f -- "$partial"' EXIT
trap 'exit 0' TERM INT

dump_database() {
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    partial="$(mktemp "$backup_dir/.field-shift-$stamp.XXXXXX")" || return 1
    suffix="${partial##*.}"
    archive="$backup_dir/field-shift-$stamp-$suffix.dump"
    if pg_dump --format=custom --lock-wait-timeout=10s --file="$partial" &&
       pg_restore --list "$partial" >/dev/null && mv -- "$partial" "$archive"; then
        partial=''
        printf 'Saved %s\n' "$archive"
        find "$backup_dir" -maxdepth 1 -type f -name 'field-shift-*.dump' -mtime "+$retention" -delete
    else
        rm -f -- "$partial"
        partial=''
        return 1
    fi
}

while :; do
    if dump_database; then
        [ "${1:-}" = "--once" ] && exit 0
        sleep "$interval" &
    else
        printf '%s\n' 'PostgreSQL backup failed' >&2
        [ "${1:-}" = "--once" ] && exit 1
        sleep 30 &
    fi
    wait "$!"
done
