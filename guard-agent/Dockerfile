ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache \
    python3 py3-pip py3-cryptography py3-aiohttp py3-aiofiles \
    nmap arp-scan iputils-ping curl jq \
    && pip3 install --no-cache-dir --break-system-packages \
        pycryptodome

#CC- s6-overlay service — HA addony vyžadují init jako s6 service, ne CMD
COPY run.sh /etc/services.d/agent/run
COPY agent/ /agent/

RUN chmod +x /etc/services.d/agent/run
