ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-pip py3-cryptography && \
    pip3 install --no-cache-dir --break-system-packages pycryptodome

#CC- s6-overlay service — HA addony vyžadují init jako s6 service, ne CMD
COPY run.sh /etc/services.d/scanner/run
COPY scanner.py /

RUN chmod +x /etc/services.d/scanner/run
