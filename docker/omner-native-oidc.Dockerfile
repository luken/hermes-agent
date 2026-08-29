FROM docker.io/nousresearch/hermes-agent:v2026.8.19@sha256:3811ed13da874fba2ac99b6d492db9a203d34cb6dccf90d886948c00d0ccec09

ARG HERMES_GIT_SHA
LABEL org.opencontainers.image.revision="${HERMES_GIT_SHA}"
LABEL org.opencontainers.image.source="https://github.com/luken/hermes-agent/tree/codex/matrix-supervisor-identity-gate"
LABEL org.opencontainers.image.base.revision="fcbd1076a93841fa88855acce810e342a5b78101"

# The pinned upstream runtime predates two fixed critical packages. Keep this
# repair layer explicit and let the workflow's immutable SBOM and Trivy report
# record the exact resolved Debian package and bundled npm dependency versions.
RUN apt-get -o Acquire::Retries=3 update && \
    apt-get -o Acquire::Retries=3 install -y --no-install-recommends --only-upgrade docker-cli && \
    npm install --global --no-audit --fund=false npm@11.18.0 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY --chown=root:root --chmod=0644 hermes_cli/config_defaults.py /opt/hermes/hermes_cli/config_defaults.py
COPY --chown=root:root --chmod=0644 hermes_cli/projects_cmd.py /opt/hermes/hermes_cli/projects_cmd.py
COPY --chown=root:root --chmod=0644 hermes_cli/projects_db.py /opt/hermes/hermes_cli/projects_db.py
COPY --chown=root:root --chmod=0644 hermes_cli/web_server.py /opt/hermes/hermes_cli/web_server.py
COPY --chown=root:root --chmod=0644 hermes_cli/dashboard_auth/middleware.py /opt/hermes/hermes_cli/dashboard_auth/middleware.py
COPY --chown=root:root --chmod=0644 hermes_cli/dashboard_auth/native_redirects.py /opt/hermes/hermes_cli/dashboard_auth/native_redirects.py
COPY --chown=root:root --chmod=0644 hermes_cli/dashboard_auth/routes.py /opt/hermes/hermes_cli/dashboard_auth/routes.py
COPY --chown=root:root --chmod=0644 plugins/dashboard_auth/self_hosted/__init__.py /opt/hermes/plugins/dashboard_auth/self_hosted/__init__.py
COPY --chown=root:root --chmod=0644 plugins/memory/hindsight/__init__.py /opt/hermes/plugins/memory/hindsight/__init__.py
COPY --chown=root:root --chmod=0644 plugins/platforms/matrix/adapter.py /opt/hermes/plugins/platforms/matrix/adapter.py
COPY --chown=root:root --chmod=0644 tui_gateway/project_tree.py /opt/hermes/tui_gateway/project_tree.py
