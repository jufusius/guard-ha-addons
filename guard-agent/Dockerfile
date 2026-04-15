ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache \
    python3 py3-pip py3-cryptography py3-aiohttp py3-aiofiles py3-pycryptodome \
    nmap arp-scan iputils-ping curl jq

COPY run.sh /
COPY agent/ /agent/

RUN chmod +x /run.sh

CMD ["/run.sh"]
