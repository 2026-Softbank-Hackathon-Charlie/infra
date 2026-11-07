FROM docker.hyperdx.io/hyperdx/hyperdx:2.39.1@sha256:619783ca66d6c81f72a0106f4013d65865ac42e064491b19c0019ffec268f316
COPY --chown=node:node patch-prometheus-auth.cjs /tmp/charlie-patch-prometheus-auth.cjs
RUN node /tmp/charlie-patch-prometheus-auth.cjs && rm /tmp/charlie-patch-prometheus-auth.cjs
