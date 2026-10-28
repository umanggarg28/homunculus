# The sandbox's proxy: Squid built with OpenSSL, which TLS inspection needs, from
# Ubuntu's own archive. The base is pinned by digest; packages are not, so each build
# gets the archive's security fixes.
FROM ubuntu:26.04@sha256:0e73b71479e7bc5f006bca872e25c3cd680dc052a0116f90d63ae363537db0dd

RUN apt-get update \
    && apt-get install -y --no-install-recommends squid-openssl openssl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY squid.conf /etc/squid/squid.conf
COPY --chmod=755 start-proxy /usr/local/bin/start-proxy

# Squid's own user from the package, never root.
USER proxy
ENTRYPOINT ["/usr/local/bin/start-proxy"]
