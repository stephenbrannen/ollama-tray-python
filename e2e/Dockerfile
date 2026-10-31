# The test container: Xvfb, a session D-Bus, the app from this checkout, and the pytest driver.
FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

FROM python:3.13.16-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f

# `socat` is the proxy the tray talks through, so a test can cut the connection.
# PySide6 loads the first group at import. Qt's xcb platform plugin needs the libxcb group.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        xvfb xauth dbus socat \
        libegl1 libgl1 libglib2.0-0t64 libxkbcommon0 libfontconfig1 libdbus-1-3 \
        libx11-xcb1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
        libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 libxkbcommon-x11-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/app

# The driver's D-Bus client and pytest live in their own environment, so the app's lock file
# and dependency groups stay as they are.
COPY e2e/requirements.txt /tmp/driver-requirements.txt
RUN uv venv /opt/driver \
    && uv pip install --python /opt/driver --require-hashes -r /tmp/driver-requirements.txt

WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

COPY e2e ./e2e
ENTRYPOINT ["/src/e2e/entrypoint.sh"]
