#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

# News AI Social Media Manager - local developer/test setup.
#
# What this script does:
#   - verifies Python >= 3.11
#   - creates/reuses .venv and installs .[dev]
#   - installs Docker automatically on Debian/Ubuntu when Docker is missing
#   - creates isolated disposable PostgreSQL 16 + Redis 7 test containers
#   - optionally captures GROQ_API_KEY without echoing it
#   - writes a git-ignored .env.test.local with test-only environment variables
#   - applies Alembic migrations and checks migration drift
#
# It intentionally does NOT:
#   - touch production/development databases
#   - reuse Stage 27 or other existing PostgreSQL/Redis containers
#   - enable live social publishing
#   - add the user to the docker group automatically
#
# Re-running this script RECREATES only these disposable containers:
#   news-ai-test-postgres
#   news-ai-test-redis

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
VENV_DIR="$REPO_ROOT/.venv"
ENV_FILE="$REPO_ROOT/.env.test.local"

POSTGRES_CONTAINER="${NEWS_AI_TEST_POSTGRES_CONTAINER:-news-ai-test-postgres}"
REDIS_CONTAINER="${NEWS_AI_TEST_REDIS_CONTAINER:-news-ai-test-redis}"
POSTGRES_IMAGE="${NEWS_AI_TEST_POSTGRES_IMAGE:-postgres:16-alpine}"
REDIS_IMAGE="${NEWS_AI_TEST_REDIS_IMAGE:-redis:7-alpine}"

POSTGRES_DB="${NEWS_AI_TEST_POSTGRES_DB:-news_ai}"
POSTGRES_USER="${NEWS_AI_TEST_POSTGRES_USER:-postgres}"
POSTGRES_PASSWORD="${NEWS_AI_TEST_POSTGRES_PASSWORD:-postgres}"

DOCKER_CMD=()
PYTHON_BIN=""

log() {
  printf '\n==> %s\n' "$*"
}

warn() {
  printf '\nWARNING: %s\n' "$*" >&2
}

die() {
  printf '\nERROR: %s\n' "$*" >&2
  exit 1
}

have() {
  command -v "$1" >/dev/null 2>&1
}

run_as_root() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  elif have sudo; then
    sudo "$@"
  else
    die "This step requires root privileges, but sudo is not installed."
  fi
}

run_docker() {
  "${DOCKER_CMD[@]}" "$@"
}

require_repo_root() {
  [[ -f "$REPO_ROOT/pyproject.toml" ]] \
    || die "setup.sh must live in the repository root next to pyproject.toml."
  cd "$REPO_ROOT"
}

find_python() {
  local candidates=()

  if [[ -n "${PYTHON_BIN_OVERRIDE:-}" ]]; then
    candidates+=("$PYTHON_BIN_OVERRIDE")
  fi

  # Prefer a current interpreter when present, but accept the project contract >=3.11.
  candidates+=(python3.14 python3.13 python3.12 python3.11 python3)

  local candidate
  for candidate in "${candidates[@]}"; do
    if have "$candidate" && "$candidate" -c \
      'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
      >/dev/null 2>&1; then
      PYTHON_BIN="$(command -v "$candidate")"
      break
    fi
  done

  [[ -n "$PYTHON_BIN" ]] || die \
    "Python >= 3.11 is required. Install a supported Python interpreter and rerun setup.sh."

  log "Using Python: $("$PYTHON_BIN" --version 2>&1) ($PYTHON_BIN)"
}

ensure_venv() {
  local recreate=0

  if [[ -x "$VENV_DIR/bin/python" ]]; then
    if ! "$VENV_DIR/bin/python" -c \
      'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
      >/dev/null 2>&1; then
      warn "Existing .venv is broken or uses unsupported Python; recreating it."
      recreate=1
    fi
  else
    recreate=1
  fi

  if [[ "$recreate" -eq 1 ]]; then
    rm -rf "$VENV_DIR"

    log "Creating virtual environment"
    if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
      if have apt-get; then
        log "Installing Python venv support"
        run_as_root apt-get update
        run_as_root apt-get install -y python3-venv
        "$PYTHON_BIN" -m venv "$VENV_DIR"
      else
        die "Unable to create .venv. Install the venv module for $PYTHON_BIN and rerun."
      fi
    fi
  else
    log "Reusing existing virtual environment"
  fi

  log "Installing project and development dependencies"
  "$VENV_DIR/bin/python" -m pip install -e '.[dev]'
}

install_docker_if_missing() {
  if have docker; then
    return
  fi

  log "Docker was not found"

  if have apt-get; then
    log "Installing Docker from the Debian/Ubuntu package repository"
    run_as_root apt-get update
    run_as_root apt-get install -y docker.io

    if have systemctl; then
      run_as_root systemctl enable --now docker
    elif have service; then
      run_as_root service docker start
    fi
  else
    die "Automatic Docker installation is currently supported only on Debian/Ubuntu. Install Docker for this host and rerun setup.sh."
  fi
}

select_docker_command() {
  # First try the current user's Docker context (including Docker Desktop/rootless).
  if docker info >/dev/null 2>&1; then
    DOCKER_CMD=(docker)
    return
  fi

  # If Docker exists but the daemon is stopped, try starting the system service.
  if have systemctl; then
    run_as_root systemctl start docker >/dev/null 2>&1 || true
  elif have service; then
    run_as_root service docker start >/dev/null 2>&1 || true
  fi

  if docker info >/dev/null 2>&1; then
    DOCKER_CMD=(docker)
    return
  fi

  # Do not silently add users to the docker group; membership is effectively root-equivalent.
  if have sudo && sudo docker info >/dev/null 2>&1; then
    DOCKER_CMD=(sudo docker)
    warn "Docker requires sudo for this user. setup.sh will use sudo docker; it will not modify docker-group membership."
    return
  fi

  die "Docker is installed but the daemon is unavailable or inaccessible."
}

wait_for_healthy() {
  local container="$1"
  local max_attempts="${2:-60}"
  local status=""
  local attempt

  for ((attempt = 1; attempt <= max_attempts; attempt++)); do
    status="$(run_docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)"
    case "$status" in
      healthy)
        return 0
        ;;
      unhealthy|exited|dead)
        run_docker logs --tail 100 "$container" >&2 || true
        die "$container entered state '$status'."
        ;;
    esac
    sleep 1
  done

  run_docker logs --tail 100 "$container" >&2 || true
  die "Timed out waiting for $container to become healthy."
}

start_test_services() {
  log "Pulling CI-matching PostgreSQL and Redis images"
  run_docker pull "$POSTGRES_IMAGE"
  run_docker pull "$REDIS_IMAGE"

  log "Recreating isolated disposable test containers"

  # Only these exact test-container names are removed. Other project/runtime
  # PostgreSQL and Redis containers are deliberately untouched.
  run_docker rm -f "$POSTGRES_CONTAINER" "$REDIS_CONTAINER" >/dev/null 2>&1 || true

  run_docker run -d \
    --name "$POSTGRES_CONTAINER" \
    --label news-ai.local-test=true \
    -e "POSTGRES_DB=$POSTGRES_DB" \
    -e "POSTGRES_USER=$POSTGRES_USER" \
    -e "POSTGRES_PASSWORD=$POSTGRES_PASSWORD" \
    -p 127.0.0.1::5432 \
    --health-cmd="pg_isready -U $POSTGRES_USER -d $POSTGRES_DB" \
    --health-interval=2s \
    --health-timeout=5s \
    --health-retries=30 \
    "$POSTGRES_IMAGE" >/dev/null

  run_docker run -d \
    --name "$REDIS_CONTAINER" \
    --label news-ai.local-test=true \
    -p 127.0.0.1::6379 \
    --health-cmd="redis-cli ping" \
    --health-interval=2s \
    --health-timeout=5s \
    --health-retries=30 \
    "$REDIS_IMAGE" >/dev/null

  wait_for_healthy "$POSTGRES_CONTAINER"
  wait_for_healthy "$REDIS_CONTAINER"

  POSTGRES_PORT="$(run_docker port "$POSTGRES_CONTAINER" 5432/tcp | awk -F: 'NR == 1 {print $NF}')"
  REDIS_PORT="$(run_docker port "$REDIS_CONTAINER" 6379/tcp | awk -F: 'NR == 1 {print $NF}')"

  [[ "$POSTGRES_PORT" =~ ^[0-9]+$ ]] || die "Could not determine PostgreSQL host port."
  [[ "$REDIS_PORT" =~ ^[0-9]+$ ]] || die "Could not determine Redis host port."

  log "PostgreSQL is healthy on 127.0.0.1:$POSTGRES_PORT"
  log "Redis is healthy on 127.0.0.1:$REDIS_PORT"
}

configure_groq() {
  GROQ_KEY=""
  RUN_LIVE_GROQ="0"

  if [[ -n "${GROQ_API_KEY:-}" ]]; then
    GROQ_KEY="$GROQ_API_KEY"
  fi

  if [[ ! -t 0 ]]; then
    if [[ -n "$GROQ_KEY" && "${NEWS_AI_RUN_LIVE_GROQ:-0}" == "1" ]]; then
      RUN_LIVE_GROQ="1"
    fi
    return
  fi

  local answer=""
  if [[ -n "$GROQ_KEY" ]]; then
    read -r -p "GROQ_API_KEY is already present in the environment. Enable the live Groq acceptance test? [y/N] " answer
  else
    read -r -p "Configure a GROQ_API_KEY for the optional live Groq acceptance test? [y/N] " answer
    if [[ "$answer" =~ ^[Yy]$ ]]; then
      read -r -s -p "GROQ_API_KEY (input hidden): " GROQ_KEY
      printf '\n'
    fi
  fi

  if [[ "$answer" =~ ^[Yy]$ && -n "$GROQ_KEY" ]]; then
    RUN_LIVE_GROQ="1"
    [[ "$GROQ_KEY" == gsk_* ]] || warn "The supplied Groq key does not start with the usual 'gsk_' prefix."
  else
    RUN_LIVE_GROQ="0"
  fi
}

write_test_env() {
  local database_url="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_PORT}/${POSTGRES_DB}"
  local redis_url="redis://127.0.0.1:${REDIS_PORT}/0"

  log "Writing test environment to $ENV_FILE"

  umask 077
  {
    printf '# Generated by setup.sh. Local testing only. Do not commit.\n'
    printf '# Source this file from Bash: source .env.test.local\n\n'
    printf 'export NEWS_AI_ENVIRONMENT=%q\n' "test"
    printf 'export NEWS_AI_CONFIG_DIR=%q\n' "$REPO_ROOT/config"
    printf 'export NEWS_AI_DATABASE_URL=%q\n' "$database_url"
    printf 'export NEWS_AI_REDIS_URL=%q\n' "$redis_url"
    printf 'export NEWS_AI_READINESS_TIMEOUT_SECONDS=%q\n' "5"

    # Keep external publication inert in this local test environment.
    printf 'export NEWS_AI_SOCIAL_MODE=%q\n' "MOCK"
    printf 'export NEWS_AI_PUBLISHING_ENABLED=%q\n' "false"
    printf 'export NEWS_AI_PUBLISHING_PAUSED=%q\n' "true"

    printf 'export NEWS_AI_RUN_LIVE_GROQ=%q\n' "$RUN_LIVE_GROQ"
    if [[ -n "$GROQ_KEY" ]]; then
      printf 'export GROQ_API_KEY=%q\n' "$GROQ_KEY"
    else
      printf 'unset GROQ_API_KEY\n'
    fi
  } > "$ENV_FILE"

  chmod 600 "$ENV_FILE"

  if have git && git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if ! git -C "$REPO_ROOT" check-ignore -q "$ENV_FILE"; then
      warn "$ENV_FILE is not ignored by Git. Do not commit it; add it to .gitignore before storing secrets."
    fi
  fi
}

prepare_database() {
  # shellcheck disable=SC1090
  source "$ENV_FILE"

  log "Applying Alembic migrations to the disposable PostgreSQL database"
  "$VENV_DIR/bin/alembic" upgrade head

  log "Checking migration drift"
  "$VENV_DIR/bin/alembic" check
}

print_summary() {
  local groq_status="disabled (the live Groq test will skip)"
  if [[ "$RUN_LIVE_GROQ" == "1" ]]; then
    groq_status="enabled"
  fi

  cat <<EOF_SUMMARY

===============================================================================
News AI local setup is ready.

Python:
  $("$VENV_DIR/bin/python" --version 2>&1)

Disposable test services:
  PostgreSQL: $POSTGRES_IMAGE -> 127.0.0.1:$POSTGRES_PORT
  Redis:      $REDIS_IMAGE -> 127.0.0.1:$REDIS_PORT
  Live Groq:  $groq_status

For every new shell:

  source .venv/bin/activate
  source .env.test.local

Run the complete test suite:

  pytest

Show skip reasons:

  pytest -q -rs

Run the exact live Groq acceptance test:

  pytest tests/integration/test_live_groq.py -vv

Run the full CI-equivalent gate:

  python -m pip install -e '.[dev]'
  ruff check .
  ruff format --check apps packages migrations tests
  python -m compileall -q apps packages migrations tests
  alembic upgrade head
  alembic check
  pytest
  alembic downgrade base
  alembic upgrade head

IMPORTANT:
  The downgrade/upgrade round trip is safe here because NEWS_AI_DATABASE_URL points
  at the disposable test container created by setup.sh. Never run that sequence
  against a development or production database.

Stop/remove only the disposable test services:

  ${DOCKER_CMD[*]} rm -f $POSTGRES_CONTAINER $REDIS_CONTAINER

The Docker images are kept locally so future setup runs are faster.
===============================================================================
EOF_SUMMARY
}

main() {
  require_repo_root
  find_python
  ensure_venv
  install_docker_if_missing
  select_docker_command
  start_test_services
  configure_groq
  write_test_env
  prepare_database
  print_summary
}

main "$@"
