# Test fixture only: controlled equivalent of the V1 already-root setup.
# No application Deployment uses this image. No packages or network needed.
FROM project5-backend:native
USER 0
RUN mkdir /opt/phase3-control && \
    cp /usr/local/bin/python3.13 /opt/phase3-control/helper && \
    chmod 4755 /opt/phase3-control/helper && \
    printf SYNTHETIC-ROOT-ONLY > /opt/phase3-control/canary && \
    chmod 0600 /opt/phase3-control/canary
USER 65532:65532
