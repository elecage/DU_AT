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

if ! command -v git >/dev/null 2>&1; then
    echo "Git is required. Install Xcode Command Line Tools or Git, then rerun this script." >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is required. Install Python 3, then rerun this script." >&2
    exit 1
fi

if command -v brew >/dev/null 2>&1; then
    echo "Installing host packages with Homebrew..."
    brew update
    brew install cmake ninja dfu-util ccache
else
    echo "Homebrew not found. ESP-IDF install may still work if host tools are already present."
fi

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
