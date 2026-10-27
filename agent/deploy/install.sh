#!/usr/bin/env bash
set -euo pipefail
umask 022

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo: sudo bash deploy/install.sh" >&2
    exit 1
fi
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR=/opt/glorify-control-agent
CONFIG_FILE=/etc/glorify-control-agent/agent.env
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3, 12), "Python >= 3.12 is required"'
command -v systemctl >/dev/null
getent group glorify-agent >/dev/null || groupadd --system glorify-agent
id glorify-agent >/dev/null 2>&1 || useradd --system --gid glorify-agent --home-dir /var/lib/glorify-control-agent --shell /usr/sbin/nologin glorify-agent
install -d -m 0755 "$INSTALL_DIR" /etc/glorify-control-agent
install -d -o glorify-agent -g glorify-agent -m 0700 /var/lib/glorify-control-agent

# Snapshot sources before stopping an existing installation; includes no .env or spool.
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf -- "$BUILD_DIR"' EXIT
cp "$SOURCE_DIR/pyproject.toml" "$BUILD_DIR/"
cp -R "$SOURCE_DIR/src" "$BUILD_DIR/"
cp "$SOURCE_DIR/requirements.lock" "$SOURCE_DIR/requirements-build.lock" "$BUILD_DIR/"
"$PYTHON_BIN" -m venv "$BUILD_DIR/venv"
"$BUILD_DIR/venv/bin/python" -m pip install --require-hashes -r "$BUILD_DIR/requirements-build.lock"
"$BUILD_DIR/venv/bin/python" -m pip download --require-hashes --only-binary=:all: --dest "$BUILD_DIR/wheels" -r "$BUILD_DIR/requirements.lock"
# Build a wheel first so dependency/build failures leave the existing service running.
"$BUILD_DIR/venv/bin/python" -m pip wheel --no-deps --no-build-isolation --wheel-dir "$BUILD_DIR/wheels" "$BUILD_DIR"
if systemctl is-active --quiet glorify-control-agent; then
    systemctl stop glorify-control-agent
fi
"$PYTHON_BIN" -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/python" -m pip install --no-index --find-links "$BUILD_DIR/wheels" --require-hashes -r "$BUILD_DIR/requirements.lock"
"$INSTALL_DIR/venv/bin/python" -m pip install --no-index --find-links "$BUILD_DIR/wheels" --force-reinstall --no-deps glorify-control-agent
install -m 0644 "$SOURCE_DIR/deploy/glorify-control-agent.service" /etc/systemd/system/glorify-control-agent.service
install -m 0644 "$SOURCE_DIR/.env.example" "$INSTALL_DIR/agent.env.example"
install -d -m 0755 /usr/local/bin
ln -sfn "$INSTALL_DIR/venv/bin/glorify-control-agent" /usr/local/bin/glorify-control-agent
systemctl daemon-reload
if [[ ! -f "$CONFIG_FILE" ]]; then
    install -m 0600 "$SOURCE_DIR/.env.example" "$CONFIG_FILE"
    echo "Template created at $CONFIG_FILE. Set owned URLs, system_id and token."
    echo "Then: sudo systemctl enable --now glorify-control-agent"
    exit 0
fi
chown root:root "$CONFIG_FILE"
chmod 0600 "$CONFIG_FILE"
# EnvironmentFile is parsed by systemd; never source an environment file as shell code.
if grep -q 'REPLACE_ME' "$CONFIG_FILE"; then
    echo "Complete $CONFIG_FILE and run: sudo systemctl enable --now glorify-control-agent"
    exit 0
fi
systemctl enable glorify-control-agent
systemctl restart glorify-control-agent
systemctl --no-pager status glorify-control-agent
