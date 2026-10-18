#!/usr/bin/env bash
# Usage: bash ./data/setup.sh

set -e

echo "=== Detecting Operating System ==="

detect_os() {
    case "$OSTYPE" in
        linux*)
            if [ -f /etc/os-release ]; then
                . /etc/os-release
                case "$ID" in
                    ubuntu|debian)
                        echo "ubuntu"
                        ;;
                    *)
                        # Fallback for Ubuntu-derived / Debian-like distros
                        if [[ "$ID_LIKE" =~ "ubuntu" ]] || [[ "$ID_LIKE" =~ "debian" ]]; then
                            echo "ubuntu"
                        else
                            echo "unsupported_linux"
                        fi
                        ;;
                esac
            else
                echo "unsupported_linux"
            fi
            ;;
        darwin*)
            echo "macos"
            ;;
        msys*|cygwin*|mingw*)
            echo "windows"
            ;;
        *)
            echo "unknown"
            ;;
    esac
}

OS=$(detect_os)

case "$OS" in
    ubuntu)
        echo "Detected OS: Ubuntu / Debian"
        echo "Installing MariaDB..."
        sudo apt-get update
        sudo apt-get install -y mariadb-server mariadb-client
        sudo systemctl enable mariadb
        sudo systemctl start mariadb
        echo "MariaDB installation and startup complete."
        ;;
    macos)
        echo "Detected OS: macOS"
        if ! command -v brew >/dev/null 2>&1; then
            echo "Error: Homebrew is required to install MariaDB on macOS."
            echo "Install Homebrew from https://brew.sh and rerun this script."
            exit 1
        fi
        echo "Installing MariaDB via Homebrew..."
        brew update
        brew install mariadb
        brew services start mariadb
        echo "MariaDB installation and startup complete."
        ;;
    windows)
        echo "Detected OS: Windows (via Git Bash / MSYS2 / Cygwin)"
        if command -v winget.exe >/dev/null 2>&1; then
            echo "Installing MariaDB via winget..."
            winget.exe install --id MariaDB.Server -e --accept-source-agreements --accept-package-agreements
        elif command -v choco.exe >/dev/null 2>&1; then
            echo "Installing MariaDB via Chocolatey..."
            choco.exe install mariadb -y
        elif command -v scoop.exe >/dev/null 2>&1; then
            echo "Installing MariaDB via Scoop..."
            scoop.exe install mariadb
        else
            echo "Error: No supported package manager found (winget, choco, or scoop)."
            echo "Please install winget, Chocolatey, or Scoop, or download MariaDB manually from https://mariadb.org/download/"
            exit 1
        fi
        echo "MariaDB installation complete."
        ;;
    unsupported_linux)
        echo "Detected Linux distribution is not directly supported by this script (Ubuntu/Debian expected)."
        exit 1
        ;;
    *)
        echo "Unsupported or unrecognized operating system: $OSTYPE"
        exit 1
        ;;
esac

