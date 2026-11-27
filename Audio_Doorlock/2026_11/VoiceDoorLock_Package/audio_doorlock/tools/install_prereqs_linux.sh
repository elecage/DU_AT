#!/usr/bin/env bash
set -euo pipefail

IDF_VERSION="${IDF_VERSION:-v5.4.1}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
IDF_PATH="${IDF_PATH:-${PROJECT_ROOT}/.espressif/esp-idf-${IDF_VERSION#v}}"
export IDF_TOOLS_PATH="${IDF_TOOLS_PATH:-${PROJECT_ROOT}/.espressif/tools}"

echo "Project root: ${PROJECT_ROOT}"
echo "ESP-IDF path: ${IDF_PATH}"
echo "ESP-IDF tools path: ${IDF_TOOLS_PATH}"

check_idf_python_venv() {
    if [ -z "${IDF_PYTHON_ENV_PATH:-}" ]; then
        echo "IDF_PYTHON_ENV_PATH is not set after export.sh." >&2
        exit 1
    fi

    if [ ! -x "${IDF_PYTHON_ENV_PATH}/bin/python" ]; then
        echo "ESP-IDF Python venv interpreter not found: ${IDF_PYTHON_ENV_PATH}/bin/python" >&2
        exit 1
    fi

    "${IDF_PYTHON_ENV_PATH}/bin/python" -c 'import sys; raise SystemExit(0 if sys.prefix != getattr(sys, "base_prefix", sys.prefix) else 1)'
    echo "ESP-IDF Python venv: ${IDF_PYTHON_ENV_PATH}"
}

install_host_packages() {
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update
        sudo apt-get install -y git wget flex bison gperf python3 python3-pip python3-venv \
            cmake ninja-build ccache libffi-dev libssl-dev dfu-util libusb-1.0-0
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y git wget flex bison gperf python3 python3-pip cmake ninja-build \
            ccache libffi-devel openssl-devel dfu-util libusb
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -Sy --needed git wget flex bison gperf python python-pip cmake ninja \
            ccache libffi openssl dfu-util libusb
    else
        echo "No supported package manager found. Install ESP-IDF host packages manually." >&2
    fi
}

install_host_packages

if [ ! -d "${IDF_PATH}" ]; then
    mkdir -p "$(dirname "${IDF_PATH}")"
    echo "Cloning ESP-IDF ${IDF_VERSION}..."
    git clone --branch "${IDF_VERSION}" --recursive https://github.com/espressif/esp-idf.git "${IDF_PATH}"
else
    echo "ESP-IDF directory already exists. Skipping clone."
fi

echo "Installing ESP-IDF tools for ESP32-C3..."
"${IDF_PATH}/install.sh" esp32c3

echo "Exporting ESP-IDF environment and setting project target..."
# shellcheck disable=SC1091
. "${IDF_PATH}/export.sh"
check_idf_python_venv
idf.py set-target esp32c3

echo "Resolving project managed components..."
idf.py reconfigure

cat <<EOF

Prerequisites are ready.
For a new shell, run:
  export IDF_TOOLS_PATH="${IDF_TOOLS_PATH}"
  . "${IDF_PATH}/export.sh"
Then build with:
  idf.py build
EOF
