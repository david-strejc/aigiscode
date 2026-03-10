#!/usr/bin/env bash
# AigisCode Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/david-strejc/aigiscode/main/install.sh | bash
#    or: wget -qO- https://raw.githubusercontent.com/david-strejc/aigiscode/main/install.sh | bash
#
# Environment variables:
#   AIGISCODE_VERSION  - version to install (default: latest)
#   AIGISCODE_DIR      - installation directory (default: ~/.aigiscode)
#   NO_COLOR           - disable colored output

set -euo pipefail

VERSION="${AIGISCODE_VERSION:-0.1.0}"
INSTALL_DIR="${AIGISCODE_DIR:-$HOME/.aigiscode}"
REPO="david-strejc/aigiscode"
MIN_PYTHON="3.12"

# ── Colors ────────────────────────────────────────────────────────────────────
if [ -z "${NO_COLOR:-}" ] && [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    BLUE='\033[0;34m'
    PURPLE='\033[0;35m'
    BOLD='\033[1m'
    RESET='\033[0m'
else
    RED='' GREEN='' YELLOW='' BLUE='' PURPLE='' BOLD='' RESET=''
fi

info()  { printf "${BLUE}▸${RESET} %s\n" "$*"; }
ok()    { printf "${GREEN}✓${RESET} %s\n" "$*"; }
warn()  { printf "${YELLOW}!${RESET} %s\n" "$*"; }
err()   { printf "${RED}✗${RESET} %s\n" "$*" >&2; }
die()   { err "$*"; exit 1; }

banner() {
    printf "\n"
    printf "${PURPLE}${BOLD}"
    printf "    ╔═══════════════════════════════════════╗\n"
    printf "    ║     🛡️  AigisCode Installer  v%s   ║\n" "$VERSION"
    printf "    ║     AI-Powered Code Guardian          ║\n"
    printf "    ╚═══════════════════════════════════════╝\n"
    printf "${RESET}\n"
}

# ── OS / Architecture Detection ───────────────────────────────────────────────
detect_os() {
    case "$(uname -s)" in
        Linux*)  echo "linux" ;;
        Darwin*) echo "macos" ;;
        CYGWIN*|MINGW*|MSYS*) echo "windows" ;;
        *)       die "Unsupported operating system: $(uname -s)" ;;
    esac
}

detect_arch() {
    case "$(uname -m)" in
        x86_64|amd64)  echo "x64" ;;
        arm64|aarch64) echo "arm64" ;;
        *)             echo "$(uname -m)" ;;
    esac
}

# ── Python Detection ──────────────────────────────────────────────────────────
find_python() {
    local candidates=("python3.13" "python3.12" "python3" "python")
    for cmd in "${candidates[@]}"; do
        if command -v "$cmd" &>/dev/null; then
            local ver
            ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null) || continue
            local major minor
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [ "$major" -ge 3 ] && [ "$minor" -ge 12 ]; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

# ── pipx Detection ────────────────────────────────────────────────────────────
find_pipx() {
    if command -v pipx &>/dev/null; then
        echo "pipx"
        return 0
    fi
    return 1
}

# ── uv Detection ──────────────────────────────────────────────────────────────
find_uv() {
    if command -v uv &>/dev/null; then
        echo "uv"
        return 0
    fi
    return 1
}

# ── Installation Methods ──────────────────────────────────────────────────────
install_with_uv() {
    info "Installing with uv (fastest)..."
    uv tool install "aigiscode==$VERSION" 2>/dev/null || \
    uv tool install "aigiscode>=$VERSION" 2>/dev/null || \
    uv tool install "git+https://github.com/${REPO}.git@v${VERSION}" 2>/dev/null || \
    uv tool install "git+https://github.com/${REPO}.git" || \
    die "Failed to install with uv"
}

install_with_pipx() {
    info "Installing with pipx..."
    pipx install "aigiscode==$VERSION" 2>/dev/null || \
    pipx install "aigiscode>=$VERSION" 2>/dev/null || \
    pipx install "git+https://github.com/${REPO}.git@v${VERSION}" 2>/dev/null || \
    pipx install "git+https://github.com/${REPO}.git" || \
    die "Failed to install with pipx"
}

install_with_venv() {
    local python_cmd="$1"

    info "Installing into isolated venv at ${INSTALL_DIR}..."
    mkdir -p "$INSTALL_DIR"

    # Create virtual environment
    "$python_cmd" -m venv "$INSTALL_DIR/venv"
    local pip="$INSTALL_DIR/venv/bin/pip"

    # Upgrade pip silently
    "$pip" install --upgrade pip --quiet 2>/dev/null

    # Install aigiscode
    "$pip" install "aigiscode==$VERSION" --quiet 2>/dev/null || \
    "$pip" install "aigiscode>=$VERSION" --quiet 2>/dev/null || \
    "$pip" install "git+https://github.com/${REPO}.git@v${VERSION}" --quiet 2>/dev/null || \
    "$pip" install "git+https://github.com/${REPO}.git" --quiet || \
    die "Failed to install aigiscode"

    # Create wrapper script
    local bin_dir
    if [ "$(detect_os)" = "windows" ]; then
        bin_dir="$INSTALL_DIR/venv/Scripts"
    else
        bin_dir="$INSTALL_DIR/venv/bin"
    fi

    # Symlink or add to PATH
    local target_dir="$HOME/.local/bin"
    mkdir -p "$target_dir"

    if [ -f "$bin_dir/aigiscode" ]; then
        ln -sf "$bin_dir/aigiscode" "$target_dir/aigiscode"
        ok "Linked aigiscode to $target_dir/aigiscode"
    fi
}

# ── PATH Setup ────────────────────────────────────────────────────────────────
ensure_path() {
    local target_dir="$HOME/.local/bin"
    if [[ ":$PATH:" != *":$target_dir:"* ]]; then
        warn "$target_dir is not in your PATH"

        local shell_name
        shell_name=$(basename "${SHELL:-/bin/bash}")
        local rc_file=""

        case "$shell_name" in
            zsh)  rc_file="$HOME/.zshrc" ;;
            bash) rc_file="$HOME/.bashrc" ;;
            fish) rc_file="$HOME/.config/fish/config.fish" ;;
        esac

        if [ -n "$rc_file" ]; then
            local path_line='export PATH="$HOME/.local/bin:$PATH"'
            if [ "$shell_name" = "fish" ]; then
                path_line='set -gx PATH $HOME/.local/bin $PATH'
            fi

            if [ -f "$rc_file" ] && ! grep -q '.local/bin' "$rc_file" 2>/dev/null; then
                echo "" >> "$rc_file"
                echo "# Added by AigisCode installer" >> "$rc_file"
                echo "$path_line" >> "$rc_file"
                ok "Added $target_dir to PATH in $rc_file"
                info "Run: source $rc_file  (or restart your terminal)"
            fi
        fi
    fi
}

# ── Verify Installation ──────────────────────────────────────────────────────
verify_install() {
    # Check common locations
    local aigis_cmd=""
    if command -v aigiscode &>/dev/null; then
        aigis_cmd="aigiscode"
    elif [ -f "$HOME/.local/bin/aigiscode" ]; then
        aigis_cmd="$HOME/.local/bin/aigiscode"
    elif [ -f "$INSTALL_DIR/venv/bin/aigiscode" ]; then
        aigis_cmd="$INSTALL_DIR/venv/bin/aigiscode"
    fi

    if [ -n "$aigis_cmd" ]; then
        local installed_ver
        installed_ver=$("$aigis_cmd" --version 2>/dev/null || echo "unknown")
        ok "AigisCode installed successfully! (${installed_ver})"
    else
        ok "AigisCode installed. You may need to restart your terminal."
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    banner

    local os arch
    os=$(detect_os)
    arch=$(detect_arch)
    info "Detected: ${os}/${arch}"

    # Find Python 3.12+
    local python_cmd
    python_cmd=$(find_python) || die "Python ${MIN_PYTHON}+ is required but not found.

Install Python:
  macOS:   brew install python@3.12
  Ubuntu:  sudo apt install python3.12 python3.12-venv
  Fedora:  sudo dnf install python3.12
  Windows: https://python.org/downloads/"

    local python_ver
    python_ver=$("$python_cmd" --version 2>&1)
    ok "Found ${python_ver}"

    # Try installers in order: uv > pipx > venv
    local uv_cmd pipx_cmd
    if uv_cmd=$(find_uv); then
        ok "Found uv"
        install_with_uv
    elif pipx_cmd=$(find_pipx); then
        ok "Found pipx"
        install_with_pipx
    else
        info "No uv or pipx found, using venv"
        install_with_venv "$python_cmd"
        ensure_path
    fi

    verify_install

    printf "\n"
    printf "${BOLD}Get started:${RESET}\n"
    printf "  ${GREEN}aigiscode analyze .${RESET}    # Analyze current directory\n"
    printf "  ${GREEN}aigiscode --help${RESET}       # See all commands\n"
    printf "\n"
    printf "  ${BLUE}Docs:${RESET}    https://aigiscode.com/docs\n"
    printf "  ${BLUE}GitHub:${RESET}  https://github.com/${REPO}\n"
    printf "\n"
}

main "$@"
