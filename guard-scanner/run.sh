#!/usr/bin/with-contenv bashio
# Guard Network Scanner — HA Addon entrypoint

bashio::log.info "Starting Guard Network Scanner..."

export GUARD_API_KEY=$(bashio::config 'api_key')
export GUARD_SERVER_URL=$(bashio::config 'server_url')
export SCAN_INTERVAL_MINUTES=$(bashio::config 'scan_interval')
export TUYA_SCAN_ENABLED=$(bashio::config 'tuya_scan')

if [ -z "$GUARD_API_KEY" ] || [ "$GUARD_API_KEY" = "null" ]; then
    bashio::log.error "API key is not configured! Go to Add-on Configuration and enter your Guard API key."
    bashio::exit.nok
fi

bashio::log.info "Server: ${GUARD_SERVER_URL}"
bashio::log.info "Scan interval: ${SCAN_INTERVAL_MINUTES}m"
bashio::log.info "Tuya scan: ${TUYA_SCAN_ENABLED}"

exec python3 /scanner.py
