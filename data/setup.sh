#!/usr/bin/env bash
# Usage: bash ./data/setup.sh
# Sets up MariaDB 11.8+ (required for native VECTOR and VECTOR INDEX support)

set -e

echo "=== Detecting Operating System ==="

detect_os() {
    case "$OSTYPE" in
        linux*)
            if [ -f /etc/os-release ]; then
                . /etc/os-release
                case "$ID" in
                    ubuntu|debian|linuxmint|pop|elementary|zorin)
                        echo "debian_ubuntu"
                        ;;
                    rhel|centos|fedora|rocky|almalinux)
                        echo "rhel"
                        ;;
                    *)
                        if [[ "$ID_LIKE" =~ "ubuntu" ]] || [[ "$ID_LIKE" =~ "debian" ]]; then
                            echo "debian_ubuntu"
                        elif [[ "$ID_LIKE" =~ "rhel" ]] || [[ "$ID_LIKE" =~ "fedora" ]] || [[ "$ID_LIKE" =~ "centos" ]]; then
                            echo "rhel"
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

install_debian_ubuntu() {
    echo "Detected OS: Debian / Ubuntu or derivative"
    echo "Installing prerequisites (curl, ca-certificates)..."
    sudo apt-get update
    sudo apt-get install -y curl ca-certificates apt-transport-https

    local os_type=""
    local os_version=""
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        if [ "$ID" = "debian" ] || [[ "$ID_LIKE" =~ "debian" && ! "$ID_LIKE" =~ "ubuntu" ]]; then
            os_type="debian"
            os_version="${VERSION_CODENAME:-bookworm}"
        else
            os_type="ubuntu"
            os_version="${UBUNTU_CODENAME:-${VERSION_CODENAME:-noble}}"
        fi
    fi

    echo "Configuring official MariaDB repository (11.8+) for $os_type ($os_version)..."
    curl -LsS https://r.mariadb.com/downloads/mariadb_repo_setup | sudo bash -s -- \
        --mariadb-server-version="11.8" \
        --os-type="$os_type" \
        --os-version="$os_version"

    echo "Installing / upgrading MariaDB Server and Client..."
    sudo apt-get update
    sudo apt-get install -y mariadb-server mariadb-client
    sudo systemctl enable mariadb
    sudo systemctl restart mariadb
    echo "MariaDB installation and startup complete."
}

install_rhel() {
    echo "Detected OS: RHEL / CentOS / Fedora / AlmaLinux / Rocky Linux"
    echo "Configuring official MariaDB repository (11.8+)..."
    curl -LsS https://r.mariadb.com/downloads/mariadb_repo_setup | sudo bash -s -- \
        --mariadb-server-version="11.8"

    echo "Installing / upgrading MariaDB Server and Client..."
    if command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y MariaDB-server MariaDB-client
    else
        sudo yum install -y MariaDB-server MariaDB-client
    fi
    sudo systemctl enable mariadb
    sudo systemctl restart mariadb
    echo "MariaDB installation and startup complete."
}

install_macos() {
    echo "Detected OS: macOS"
    if ! command -v brew >/dev/null 2>&1; then
        echo "Error: Homebrew is required to install MariaDB on macOS."
        echo "Install Homebrew from https://brew.sh and rerun this script."
        exit 1
    fi
    echo "Installing / upgrading MariaDB via Homebrew..."
    brew update
    if brew list mariadb >/dev/null 2>&1; then
        brew upgrade mariadb || true
    else
        brew install mariadb
    fi
    brew services restart mariadb 2>/dev/null || brew services start mariadb
    echo "MariaDB installation and startup complete."
}

install_windows() {
    echo "Detected OS: Windows (via Git Bash / MSYS2 / Cygwin)"
    echo "Installing / upgrading MariaDB 11.8+..."
    if command -v winget.exe >/dev/null 2>&1; then
        echo "Using winget..."
        winget.exe upgrade --id MariaDB.Server -e --accept-source-agreements --accept-package-agreements 2>/dev/null || \
        winget.exe install --id MariaDB.Server -e --accept-source-agreements --accept-package-agreements
    elif command -v choco.exe >/dev/null 2>&1; then
        echo "Using Chocolatey..."
        choco.exe upgrade mariadb -y || choco.exe install mariadb -y
    elif command -v scoop.exe >/dev/null 2>&1; then
        echo "Using Scoop..."
        scoop.exe update mariadb || scoop.exe install mariadb
    else
        echo "Error: No supported package manager found (winget, choco, or scoop)."
        echo "Please download and install MariaDB 11.8+ manually from https://mariadb.org/download/"
        exit 1
    fi
    echo "MariaDB installation complete."
}

OS=$(detect_os)

case "$OS" in
    debian_ubuntu)
        install_debian_ubuntu
        ;;
    rhel)
        install_rhel
        ;;
    macos)
        install_macos
        ;;
    windows)
        install_windows
        ;;
    unsupported_linux)
        echo "Detected Linux distribution is not directly supported by this script."
        echo "Please configure the MariaDB repository manually from https://mariadb.org/download/"
        exit 1
        ;;
    *)
        echo "Unsupported or unrecognized operating system: $OSTYPE"
        exit 1
        ;;
esac

echo ""
echo "=== Verifying MariaDB Version ==="
if command -v mariadb >/dev/null 2>&1; then
    mariadb --version
    echo "Note: MariaDB 11.8+ provides native VECTOR support required by data/DDL.sql."
elif command -v mysql >/dev/null 2>&1; then
    mysql --version
fi
