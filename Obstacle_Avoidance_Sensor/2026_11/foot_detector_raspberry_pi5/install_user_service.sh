#!/usr/bin/env bash
set -euo pipefail

project_dir="${1:-${HOME}/foot_detector}"
unit_name="reverse-foot-camera.service"
unit_dir="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"

for required_path in \
  "${project_dir}/.venv/bin/python" \
  "${project_dir}/best.onnx" \
  "${project_dir}/reverse_camera_controller.py" \
  "${project_dir}/run_foot_onnx_usb_pi5.py" \
  "${project_dir}/${unit_name}"
do
  if [[ ! -e "${required_path}" ]]; then
    echo "필수 파일이 없습니다: ${required_path}" >&2
    exit 1
  fi
done

mkdir -p "${unit_dir}"
cp -f "${project_dir}/${unit_name}" "${unit_dir}/${unit_name}"

systemctl --user import-environment DISPLAY XDG_RUNTIME_DIR
if [[ -n "${XAUTHORITY:-}" ]]; then
  systemctl --user import-environment XAUTHORITY
fi

systemctl --user daemon-reload
systemctl --user enable "${unit_name}"
systemctl --user restart "${unit_name}"

echo
systemctl --user --no-pager --full status "${unit_name}" || true
echo
echo "실시간 로그: journalctl --user -u ${unit_name} -f"
