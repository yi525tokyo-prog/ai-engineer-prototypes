# syntax=docker/dockerfile:1
# Regent, hosted: the page, the API, the loop, the reasoning/coding workers (Claude Code) and the
# browser Regent tests its apps with. Data is restored from and saved to the host's backup store
# by regent.cloud; see deploy/cloudflare.
FROM mirror.gcr.io/library/node:22-bookworm-slim AS node

FROM mirror.gcr.io/library/python:3.12-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
WORKDIR /app
COPY pyproject.toml README.md ./
# Everything that downloads is in one step. Behind a TLS-inspecting proxy, pass its CA with
# `--secret id=build_ca,src=...`: it is used while building and is not kept in the image.
RUN --mount=type=secret,id=build_ca,required=false,mode=0444 \
    set -e; \
    if [ -f /run/secrets/build_ca ]; then \
      export SSL_CERT_FILE=/run/secrets/build_ca NODE_EXTRA_CA_CERTS=/run/secrets/build_ca PIP_CERT=/run/secrets/build_ca; \
      printf 'Acquire::https::CaInfo "/run/secrets/build_ca";\n' > /etc/apt/apt.conf.d/99build; \
      if [ -n "${HTTPS_PROXY:-}" ]; then \
        printf 'Acquire::https::Proxy "%s";\n' "$HTTPS_PROXY" >> /etc/apt/apt.conf.d/99build; \
        sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources; \
      fi; \
    fi; \
    ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm; \
    ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx; \
    npm install -g @anthropic-ai/claude-code; npm cache clean --force; \
    mkdir -p regent && touch regent/__init__.py && pip install --no-cache-dir ".[browser]" && rm -rf regent build; \
    python -m playwright install --with-deps chromium; \
    rm -f /etc/apt/apt.conf.d/99build; \
    sed -i 's|https://deb.debian.org|http://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources; \
    rm -rf /var/lib/apt/lists/* /root/.npm
# Regent itself last, so an update only rebuilds this small layer
COPY regent ./regent
RUN pip install --no-cache-dir --no-deps --force-reinstall . && rm -rf build
ENV REGENT_HOME=/data/regent PORT=8080 PYTHONUNBUFFERED=1
RUN mkdir -p /data/regent && touch /data/regent/.browser-ready
EXPOSE 8080
CMD ["python", "-m", "regent.cloud"]
