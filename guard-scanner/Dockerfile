ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-pip py3-cryptography && \
    pip3 install --no-cache-dir --break-system-packages pycryptodome

COPY run.sh /
COPY scanner.py /

RUN chmod +x /run.sh

CMD ["/run.sh"]
