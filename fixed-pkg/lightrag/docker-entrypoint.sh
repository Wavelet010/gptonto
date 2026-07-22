#!/bin/sh
set -e

# Run the server as the non-root "lightrag" user (CIS Docker 4.1) while staying
# compatible with existing deployments whose bind-mounted data is root-owned.
#
# Image-internal chown cannot fix runtime mounts, so when the container starts
# as root we chown the data dirs and then drop privileges via gosu. When the
# orchestrator already starts us as non-root (compose `user:` / k8s
# `runAsUser`), we skip the chown and exec directly.

# Preserve the pre-split behavior where `docker run <image> --port 9622` appended
# flags to the server. Now that ENTRYPOINT is this script, a first arg starting
# with "-" means the user only passed flags, so prepend the default command.
if [ "${1#-}" != "$1" ]; then
    set -- python -m lightrag.api.lightrag_server "$@"
fi

if [ "$(id -u)" = "0" ]; then
    # Sync mounted source code into site-packages so that ./update.sh
    # (rsync + restart) works for Python code, frontend, and profiles
    # without rebuilding the Docker image.
    _pkg="/usr/local/lib/python3.12/site-packages/lightrag"
    if [ -d /app/lightrag/lightrag ]; then
        # Python source — subdirectories (rm+cp = rsync --delete equivalent)
        for _sub in api edp kg parser; do
            if [ -d /app/lightrag/lightrag/"$_sub" ]; then
                rm -rf "$_pkg/$_sub" 2>/dev/null || true
                cp -r /app/lightrag/lightrag/"$_sub" "$_pkg/" 2>/dev/null || true
            fi
        done
        # Python source — root-level .py files
        if [ -d /app/lightrag/lightrag ]; then
            cp /app/lightrag/lightrag/*.py "$_pkg/" 2>/dev/null || true
        fi
        # EDP profiles
        if [ -d /app/lightrag/lightrag/edp/profiles ]; then
            mkdir -p "$_pkg/edp/profiles"
            cp -r /app/lightrag/lightrag/edp/profiles/* "$_pkg/edp/profiles/" 2>/dev/null || true
        fi
        # Frontend (webui)
        if [ -d /app/lightrag/lightrag/api/webui ]; then
            cp -r /app/lightrag/lightrag/api/webui "$_pkg/api/" 2>/dev/null || true
        fi
    fi
    # Take ownership of the writable data locations so the dropped-privilege
    # process can read/write them, covering bind-mounts/PVCs whose host content
    # is root-owned. We create+chown /app/data (the default home for all data)
    # plus any custom dirs configured via env (WORKING_DIR/INPUT_DIR/PROMPT_DIR/
    # TIKTOKEN_CACHE_DIR/LOG_DIR), so deployments that point these outside
    # /app/data keep working. The mkdir matters when only the parent is mounted
    # (e.g. a bind mount/PVC at /data with WORKING_DIR=/data/site01/storage): the
    # leaf does not exist yet, and once we drop to uid 1000 the server can no
    # longer mkdir it under the root-owned parent. Creating it here as root and
    # handing it to lightrag avoids that PermissionError.
    #
    # ERROR_LOG/ACCESS_LOG (gunicorn) are *file* paths, so we chown their parent
    # directory rather than the file itself. Unset values and system roots are
    # skipped; read-only mounts fail the mkdir/chown harmlessly.
    _error_log_dir=""
    _access_log_dir=""
    [ -n "$ERROR_LOG" ] && _error_log_dir=$(dirname "$ERROR_LOG")
    [ -n "$ACCESS_LOG" ] && _access_log_dir=$(dirname "$ACCESS_LOG")
    for _d in /app/data "$WORKING_DIR" "$INPUT_DIR" "$PROMPT_DIR" \
        "$TIKTOKEN_CACHE_DIR" "$LOG_DIR" "$_error_log_dir" "$_access_log_dir"; do
        case "$_d" in
            ""|.|/|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/proc|/root|/run|/sbin|/sys|/usr|/var) continue ;;
        esac
        mkdir -p "$_d" 2>/dev/null || true
        chown -R lightrag:lightrag "$_d" 2>/dev/null || true
    done
    # NOTE: we deliberately do NOT chown /app/.env. On a bind-mount that would
    # change the *host* file's owner to uid 1000, forcing the host user to sudo
    # just to edit their config. .env only needs to be *readable* by uid 1000,
    # which the default 0644 already satisfies. A 0600 .env owned by another uid
    # must be made readable (chmod/chown on the host) or supplied via env vars
    # (compose env_file:/environment:, k8s env/envFrom).
    exec gosu lightrag "$@"
fi

exec "$@"
