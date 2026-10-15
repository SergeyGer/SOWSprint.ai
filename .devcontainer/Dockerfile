# ---------------------------------------------------------------------------
# SOWSprint.ai — development image for VS Code Dev Containers
# ---------------------------------------------------------------------------
# Deliberately NOT the production image.
#
# The runtime Dockerfile pins a non-root user with a nologin shell, copies the source in at
# build time and starts Chainlit as its entrypoint. All three are wrong for a development
# container: the source must be bind-mounted so edits are live, the shell must work, and
# nothing should start on its own. Basing this on the same `python:3.11-slim` keeps the
# Python version identical to production, which is the part that actually matters — a dev
# container running 3.12 while production runs 3.11 is a class of bug that only appears at
# deploy time.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=0 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# git is not in the slim base and VS Code needs it for source control.
# build-essential covers the occasional sdist that has no wheel for 3.11.
# ffmpeg is here because the voice path shells out to it in some configurations, and finding
# that out by having a recording fail is worse than carrying 60 MB.
RUN apt-get update \
 && apt-get install --no-install-recommends -y \
      git \
      curl \
      ca-certificates \
      build-essential \
      ffmpeg \
      less \
      procps \
      sudo \
 && rm -rf /var/lib/apt/lists/*

# A virtualenv rather than the system interpreter, matching the production image so that
# `pip list` and tracebacks look the same in both.
RUN python -m venv "$VIRTUAL_ENV" \
 && pip install --upgrade pip setuptools wheel

# Dependencies are installed as the image is built so the first attach is fast. The source is
# NOT copied: compose bind-mounts the working tree over /workspaces, and a copy here would be
# shadowed anyway.
COPY requirements.txt requirements-dev.txt /tmp/

RUN pip install -r /tmp/requirements-dev.txt \
 && rm -f /tmp/requirements.txt /tmp/requirements-dev.txt

# The `vscode` user is what the Dev Containers spec expects, and matching the UID to the host
# user is what keeps bind-mounted files owned by you rather than by root. 1000 is the first
# interactive user on almost every Linux and macOS install; on Windows with WSL 2 it is
# irrelevant because the filesystem is mounted through the VM.
ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=1000

RUN groupadd --gid "$USER_GID" "$USERNAME" 2>/dev/null || groupmod -n "$USERNAME" "$(getent group "$USER_GID" | cut -d: -f1)" \
 && useradd --uid "$USER_UID" --gid "$USER_GID" -m -s /bin/bash "$USERNAME" 2>/dev/null \
      || usermod -l "$USERNAME" -d "/home/$USERNAME" -m "$(getent passwd "$USER_UID" | cut -d: -f1)" \
 && echo "$USERNAME ALL=(root) NOPASSWD:ALL" > /etc/sudoers.d/"$USERNAME" \
 && chmod 0440 /etc/sudoers.d/"$USERNAME" \
 && mkdir -p "/home/$USERNAME/.cache/pip" \
 && chown -R "$USER_UID:$USER_GID" "/home/$USERNAME"

#> The virtualenv is already first on the image PATH, but `bash -l` re-reads /etc/profile and
# loses it — and VS Code's integrated terminal opens a login shell, so `pytest` and `ruff`
# were "command not found" in the one place a developer actually types them.
RUN printf '%s\n' \
      '# SOWSprint.ai — keep the project virtualenv first in login shells.' \
      'export VIRTUAL_ENV=/opt/venv' \
      'export PATH="/opt/venv/bin:$PATH"' \
      > /etc/profile.d/00-sowsprint-venv.sh \
 && chmod 0644 /etc/profile.d/00-sowsprint-venv.sh

# Nothing starts by itself, exactly as in the production compose file. The container exists to
# be attached to; the application is started from a terminal when wanted.
CMD ["sleep", "infinity"]
