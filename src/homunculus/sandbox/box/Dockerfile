# The sandbox: the tools a command may use, from Ubuntu's own archive. The base is pinned
# by digest; packages are not, so each build gets the archive's security fixes. There is
# no USER: the server runs the container as the operator's own user.
FROM ubuntu:26.04@sha256:0e73b71479e7bc5f006bca872e25c3cd680dc052a0116f90d63ae363537db0dd

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git jq ripgrep unzip build-essential \
        python3 python3-pip python3-venv python-is-python3 nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Ubuntu marks its Python as managed by apt, so pip refuses to install outside a virtual
# environment (PEP 668). The PEP lets an image that is never updated after it is built
# leave the marker out, and this one cannot be: its root is read-only. pip then installs
# for the user, under $HOME.
RUN rm /usr/lib/python3*/EXTERNALLY-MANAGED
