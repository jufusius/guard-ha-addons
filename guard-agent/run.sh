#!/usr/bin/with-contenv bashio
bashio::log.info "Guard Agent starting..."

CONFIG_PATH=/data/options.json
export GUARD_API_KEY=$(bashio::config 'api_key')
export GUARD_SERVER_URL=$(bashio::config 'server_url')
export GUARD_SCAN_INTERVAL=$(bashio::config 'scan_interval')
export GUARD_TUYA_SCAN=$(bashio::config 'tuya_scan')
export SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN}"

bashio::log.info "Server: ${GUARD_SERVER_URL}, Scan interval: ${GUARD_SCAN_INTERVAL}m"

exec python3 /agent/server.py
