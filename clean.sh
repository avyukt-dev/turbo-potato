#!/usr/bin/env bash

# News AI Social Media Manager - remove the local test setup created by setup.sh.
#
# Run with:
#   source ./clean.sh
#
# Sourcing is recommended because only a sourced script can unset variables in
# the current shell. Executing ./clean.sh still removes files/containers/host
# setup, but cannot change its parent shell's environment.

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  NEWS_AI_CLEAN_SOURCED=0
else
  NEWS_AI_CLEAN_SOURCED=1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
VENV_DIR="$REPO_ROOT/.venv"
ENV_FILE="$REPO_ROOT/.env.test.local"
STATE_FILE="$REPO_ROOT/.env.setup.state"

POSTGRES_CONTAINER="${NEWS_AI_TEST_POSTGRES_CONTAINER:-news-ai-test-postgres}"
REDIS_CONTAINER="${NEWS_AI_TEST_REDIS_CONTAINER:-news-ai-test-redis}"
POSTGRES_IMAGE="${NEWS_AI_TEST_POSTGRES_IMAGE:-postgres:16-alpine}"
REDIS_IMAGE="${NEWS_AI_TEST_REDIS_IMAGE:-redis:7-alpine}"

STATE_DOCKER_INSTALLED_BY_SETUP=0
STATE_DOCKER_INSTALL_METHOD=none
STATE_DOCKER_PACKAGE=none
STATE_DOCKER_AUTOSTART_BY_SETUP=0
STATE_DOCKER_STARTED_BY_SETUP=0
STATE_DOCKER_AUTOSTART_KIND=none
STATE_DESKTOP_AUTOSTART_PATH=""
STATE_POSTGRES_IMAGE_PRESENT_BEFORE=unknown
STATE_REDIS_IMAGE_PRESENT_BEFORE=unknown
STATE_VENV_SUPPORT_INSTALLED_BY_SETUP=0
STATE_VENV_SUPPORT_METHOD=none
STATE_VENV_SUPPORT_PACKAGE=none

DOCKER_CMD=()
PLATFORM=""

clean_log() {
  printf '\n==> %s\n' "$*"
}

clean_warn() {
  printf '\nWARNING: %s\n' "$*" >&2
}

clean_have() {
  command -v "$1" >/dev/null 2>&1
}

clean_run_as_root() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    "$@"
  elif clean_have sudo; then
    sudo "$@"
  else
    clean_warn "Root privileges are required for: $*"
    return 1
  fi
}

clean_detect_platform() {
  local uname_s
  uname_s="$(uname -s 2>/dev/null || true)"
  case "$uname_s" in
    Linux) PLATFORM=linux ;;
    Darwin) PLATFORM=macos ;;
    MINGW*|MSYS*|CYGWIN*) PLATFORM=windows ;;
    *) PLATFORM=unknown ;;
  esac
}

clean_load_state() {
  if [[ -f "$STATE_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$STATE_FILE"
  fi
}

clean_windows_program_files_unix() {
  local value="${PROGRAMFILES:-C:\\Program Files}"
  if clean_have cygpath; then
    cygpath -u "$value"
  else
    printf '%s\n' "/c/Program Files"
  fi
}

clean_try_start_docker() {
  case "$PLATFORM" in
    linux)
      if clean_have systemctl; then
        clean_run_as_root systemctl start docker >/dev/null 2>&1 || true
      elif clean_have rc-service; then
        clean_run_as_root rc-service docker start >/dev/null 2>&1 || true
      elif clean_have sv; then
        clean_run_as_root sv up docker >/dev/null 2>&1 || true
      elif clean_have service; then
        clean_run_as_root service docker start >/dev/null 2>&1 || true
      fi
      ;;
    macos)
      open -a Docker >/dev/null 2>&1 || true
      ;;
    windows)
      local pf app
      pf="$(clean_windows_program_files_unix)"
      app="$pf/Docker/Docker/Docker Desktop.exe"
      [[ -x "$app" ]] && "$app" >/dev/null 2>&1 &
      ;;
  esac
}

clean_select_docker() {
  local attempt direct_cli=""
  if [[ "$PLATFORM" == "macos" ]] && ! clean_have docker \
    && [[ -x /Applications/Docker.app/Contents/Resources/bin/docker ]]; then
    direct_cli="/Applications/Docker.app/Contents/Resources/bin/docker"
  elif [[ "$PLATFORM" == "windows" ]] && ! clean_have docker && ! clean_have docker.exe; then
    local pf
    pf="$(clean_windows_program_files_unix)"
    if [[ -x "$pf/Docker/Docker/resources/bin/docker.exe" ]]; then
      direct_cli="$pf/Docker/Docker/resources/bin/docker.exe"
    fi
  fi

  clean_try_start_docker
  for ((attempt = 1; attempt <= 60; attempt++)); do
    if clean_have docker && docker info >/dev/null 2>&1; then
      DOCKER_CMD=(docker)
      return 0
    fi
    if clean_have docker.exe && docker.exe info >/dev/null 2>&1; then
      DOCKER_CMD=(docker.exe)
      return 0
    fi
    if [[ -n "$direct_cli" ]] && "$direct_cli" info >/dev/null 2>&1; then
      DOCKER_CMD=("$direct_cli")
      return 0
    fi
    if [[ "$PLATFORM" == "linux" ]] && clean_have sudo \
      && sudo docker info >/dev/null 2>&1; then
      DOCKER_CMD=(sudo docker)
      return 0
    fi
    sleep 1
  done
  return 1
}

clean_docker() {
  "${DOCKER_CMD[@]}" "$@"
}

clean_remove_container() {
  local name="$1"
  if ! clean_docker container inspect "$name" >/dev/null 2>&1; then
    return 0
  fi

  local label
  label="$(clean_docker inspect -f '{{index .Config.Labels "news-ai.local-test"}}' "$name" 2>/dev/null || true)"
  if [[ "$label" != "true" ]]; then
    clean_warn "Refusing to remove '$name': it does not carry the news-ai.local-test ownership label."
    return 1
  fi

  clean_log "Removing $name and its anonymous volumes"
  clean_docker rm -fv "$name" >/dev/null || return 1
}

clean_remove_test_resources() {
  if ! clean_select_docker; then
    clean_warn "Docker is unavailable; test containers/images could not be inspected or removed."
    return 1
  fi

  clean_remove_container "$POSTGRES_CONTAINER" || true
  clean_remove_container "$REDIS_CONTAINER" || true

  if [[ "$STATE_POSTGRES_IMAGE_PRESENT_BEFORE" == "0" ]]; then
    clean_log "Removing setup-pulled image $POSTGRES_IMAGE"
    clean_docker image rm "$POSTGRES_IMAGE" >/dev/null 2>&1 \
      || clean_warn "Could not remove $POSTGRES_IMAGE; another container may still use it."
  fi
  if [[ "$STATE_REDIS_IMAGE_PRESENT_BEFORE" == "0" ]]; then
    clean_log "Removing setup-pulled image $REDIS_IMAGE"
    clean_docker image rm "$REDIS_IMAGE" >/dev/null 2>&1 \
      || clean_warn "Could not remove $REDIS_IMAGE; another container may still use it."
  fi
}

clean_stop_docker_if_started() {
  [[ "$STATE_DOCKER_STARTED_BY_SETUP" == "1" ]] || return 0

  clean_log "Stopping Docker because setup.sh started it"
  case "$PLATFORM" in
    linux)
      if clean_have systemctl; then
        clean_run_as_root systemctl stop docker >/dev/null 2>&1 || true
      elif clean_have rc-service; then
        clean_run_as_root rc-service docker stop >/dev/null 2>&1 || true
      elif clean_have sv; then
        clean_run_as_root sv down docker >/dev/null 2>&1 || true
      elif clean_have service; then
        clean_run_as_root service docker stop >/dev/null 2>&1 || true
      fi
      ;;
    macos)
      osascript -e 'quit app "Docker"' >/dev/null 2>&1 || true
      ;;
    windows)
      if clean_have powershell.exe; then
        powershell.exe -NoProfile -Command \
          "Get-Process 'Docker Desktop' -ErrorAction SilentlyContinue | Stop-Process -Force" \
          >/dev/null 2>&1 || true
      fi
      ;;
  esac
}

clean_remove_autostart() {
  [[ "$STATE_DOCKER_AUTOSTART_BY_SETUP" == "1" ]] || return 0

  clean_log "Reversing Docker autostart added by setup.sh"
  case "$STATE_DOCKER_AUTOSTART_KIND" in
    systemd)
      clean_run_as_root systemctl disable --now docker >/dev/null 2>&1 || true
      ;;
    openrc)
      clean_run_as_root rc-update del docker default >/dev/null 2>&1 || true
      clean_run_as_root rc-service docker stop >/dev/null 2>&1 || true
      ;;
    runit)
      clean_run_as_root sv down docker >/dev/null 2>&1 || true
      [[ -L /var/service/docker ]] && clean_run_as_root rm -f /var/service/docker
      ;;
    sysv)
      if clean_have chkconfig; then
        clean_run_as_root chkconfig docker off >/dev/null 2>&1 || true
      elif clean_have update-rc.d; then
        clean_run_as_root update-rc.d -f docker remove >/dev/null 2>&1 || true
      fi
      clean_have service && clean_run_as_root service docker stop >/dev/null 2>&1 || true
      ;;
    launchd)
      if [[ -n "$STATE_DESKTOP_AUTOSTART_PATH" ]]; then
        launchctl unload "$STATE_DESKTOP_AUTOSTART_PATH" >/dev/null 2>&1 || true
        rm -f "$STATE_DESKTOP_AUTOSTART_PATH"
      fi
      ;;
    windows-startup|windows-startup-wsl)
      [[ -n "$STATE_DESKTOP_AUTOSTART_PATH" ]] && rm -f "$STATE_DESKTOP_AUTOSTART_PATH"
      ;;
  esac
}

clean_uninstall_docker() {
  [[ "$STATE_DOCKER_INSTALLED_BY_SETUP" == "1" ]] || return 0

  clean_log "Uninstalling Docker because setup.sh installed it"
  case "$STATE_DOCKER_INSTALL_METHOD" in
    apt)
      clean_run_as_root apt-get remove -y "$STATE_DOCKER_PACKAGE" || true
      ;;
    apk)
      clean_run_as_root apk del "$STATE_DOCKER_PACKAGE" || true
      ;;
    dnf)
      clean_run_as_root dnf remove -y "$STATE_DOCKER_PACKAGE" || true
      ;;
    yum)
      clean_run_as_root yum remove -y "$STATE_DOCKER_PACKAGE" || true
      ;;
    pacman)
      clean_run_as_root pacman -Rns --noconfirm "$STATE_DOCKER_PACKAGE" || true
      ;;
    zypper)
      clean_run_as_root zypper --non-interactive remove "$STATE_DOCKER_PACKAGE" || true
      ;;
    xbps)
      clean_run_as_root xbps-remove -Ry "$STATE_DOCKER_PACKAGE" || true
      ;;
    eopkg)
      clean_run_as_root eopkg remove -y "$STATE_DOCKER_PACKAGE" || true
      ;;
    swupd)
      clean_run_as_root swupd bundle-remove "$STATE_DOCKER_PACKAGE" || true
      ;;
    emerge)
      clean_run_as_root emerge --depclean "$STATE_DOCKER_PACKAGE" || true
      ;;
    brew-cask)
      brew uninstall --cask "$STATE_DOCKER_PACKAGE" || true
      ;;
    mac-dmg)
      clean_run_as_root rm -rf /Applications/Docker.app || true
      ;;
    winget)
      winget.exe uninstall --exact --id "$STATE_DOCKER_PACKAGE" --silent || true
      ;;
    choco)
      choco.exe uninstall "$STATE_DOCKER_PACKAGE" -y || true
      ;;
    windows-direct)
      local pf installer installer_win
      pf="$(clean_windows_program_files_unix)"
      installer="$pf/Docker/Docker/Docker Desktop Installer.exe"
      if [[ -x "$installer" && $(command -v cygpath 2>/dev/null) ]]; then
        installer_win="$(cygpath -w "$installer")"
        powershell.exe -NoProfile -Command \
          "Start-Process -FilePath '$installer_win' -ArgumentList 'uninstall','--quiet' -Wait -Verb RunAs" \
          || true
      else
        clean_warn "Docker Desktop was installed directly, but its uninstaller could not be located."
      fi
      ;;
  esac
}

clean_uninstall_venv_support() {
  [[ "$STATE_VENV_SUPPORT_INSTALLED_BY_SETUP" == "1" ]] || return 0

  clean_log "Removing virtualenv support package installed by setup.sh"
  case "$STATE_VENV_SUPPORT_METHOD" in
    apt) clean_run_as_root apt-get remove -y "$STATE_VENV_SUPPORT_PACKAGE" || true ;;
    apk) clean_run_as_root apk del "$STATE_VENV_SUPPORT_PACKAGE" || true ;;
    dnf) clean_run_as_root dnf remove -y "$STATE_VENV_SUPPORT_PACKAGE" || true ;;
    yum) clean_run_as_root yum remove -y "$STATE_VENV_SUPPORT_PACKAGE" || true ;;
    pacman) clean_run_as_root pacman -Rns --noconfirm "$STATE_VENV_SUPPORT_PACKAGE" || true ;;
    zypper) clean_run_as_root zypper --non-interactive remove "$STATE_VENV_SUPPORT_PACKAGE" || true ;;
    xbps) clean_run_as_root xbps-remove -Ry "$STATE_VENV_SUPPORT_PACKAGE" || true ;;
  esac
}

clean_remove_files() {
  clean_log "Removing repository-local generated setup"
  rm -rf "$VENV_DIR"
  rm -f "$ENV_FILE"
}

clean_unset_environment() {
  unset NEWS_AI_ENVIRONMENT
  unset NEWS_AI_CONFIG_DIR
  unset NEWS_AI_DATABASE_URL
  unset NEWS_AI_REDIS_URL
  unset NEWS_AI_READINESS_TIMEOUT_SECONDS
  unset NEWS_AI_SOCIAL_MODE
  unset NEWS_AI_PUBLISHING_ENABLED
  unset NEWS_AI_PUBLISHING_PAUSED
  unset NEWS_AI_RUN_LIVE_GROQ
  unset GROQ_API_KEY
}

news_ai_clean_main() {
  clean_detect_platform
  clean_load_state

  clean_remove_test_resources || true
  clean_stop_docker_if_started
  clean_remove_autostart
  clean_uninstall_docker
  clean_remove_files
  clean_uninstall_venv_support
  rm -f "$STATE_FILE"
  clean_unset_environment

  clean_log "News AI local test setup cleaned."
  if [[ "$NEWS_AI_CLEAN_SOURCED" != "1" ]]; then
    clean_warn "Files/containers were cleaned, but an executed child script cannot unset variables in its parent shell. Run 'source ./clean.sh' to clear the current shell too."
  fi
}

news_ai_clean_main

# Do not leave cleanup helper functions/variables behind when sourced.
if [[ "$NEWS_AI_CLEAN_SOURCED" == "1" ]]; then
  unset -f clean_log clean_warn clean_have clean_run_as_root clean_detect_platform
  unset -f clean_load_state clean_windows_program_files_unix clean_try_start_docker
  unset -f clean_select_docker clean_docker clean_remove_container clean_remove_test_resources
  unset -f clean_stop_docker_if_started
  unset -f clean_remove_autostart clean_uninstall_docker clean_uninstall_venv_support
  unset -f clean_remove_files clean_unset_environment news_ai_clean_main
  unset SCRIPT_DIR REPO_ROOT VENV_DIR ENV_FILE STATE_FILE
  unset POSTGRES_CONTAINER REDIS_CONTAINER POSTGRES_IMAGE REDIS_IMAGE
  unset STATE_DOCKER_INSTALLED_BY_SETUP STATE_DOCKER_INSTALL_METHOD STATE_DOCKER_PACKAGE
  unset STATE_DOCKER_AUTOSTART_BY_SETUP STATE_DOCKER_STARTED_BY_SETUP STATE_DOCKER_AUTOSTART_KIND
  unset STATE_DESKTOP_AUTOSTART_PATH STATE_POSTGRES_IMAGE_PRESENT_BEFORE
  unset STATE_REDIS_IMAGE_PRESENT_BEFORE STATE_VENV_SUPPORT_INSTALLED_BY_SETUP
  unset STATE_VENV_SUPPORT_METHOD STATE_VENV_SUPPORT_PACKAGE DOCKER_CMD PLATFORM
  unset NEWS_AI_CLEAN_SOURCED
fi
