# ESP32-C3 Matter Servo Door Lock

ESP-IDF project for turning an ESP32-C3 Super Mini into a Wi-Fi Matter door lock that drives an RC servo with PWM.

The firmware exposes two Matter endpoints:

- A standard Door Lock endpoint for Google Home app control.
- An auxiliary On/Off Plug-in Unit endpoint for voice-triggered opening through Google Assistant.

Google Assistant/Nest Mini blocks voice unlock commands for real Door Lock devices for security reasons. The auxiliary switch can be renamed in Google Home, for example to `Front door button`, and used as a momentary open trigger. When the switch turns on, the servo moves to the unlocked position and automatically returns to the locked position after 5 seconds.

## Hardware

| RC Servo | ESP32-C3 Super Mini |
| --- | --- |
| Signal | GPIO4 |
| VCC | External 5 V supply |
| GND | Common GND with ESP32-C3 |

| Battery Monitor | ESP32-C3 Super Mini |
| --- | --- |
| Divider midpoint | GPIO3 |
| Divider top | Battery + through 100 kOhm |
| Divider bottom | GND through 100 kOhm |

Do not power the servo directly from the ESP32-C3 3.3 V pin. Even small servos such as SG90 can draw enough peak current to reset the board.
The battery monitor assumes a 1-cell Li-ion/LiPo battery and a 1:1 voltage divider. Adjust the constants in [main/battery_monitor.cpp](main/battery_monitor.cpp) if you use a different battery pack, ADC pin, or resistor ratio.

![](https://github.com/elecage/audio_doorlock/blob/main/img/audio_doorlock.png)
## Features

- Target: ESP32-C3
- Matter device type: Door Lock
- Auxiliary voice trigger: On/Off Plug-in Unit
- Matter hub tested with: Google Nest Mini 2nd generation + Google Home
- Servo PWM: LEDC low-speed mode, 50 Hz
- Servo signal pin: GPIO4
- Auto-relock delay for voice trigger: 5 seconds
- Battery monitor: ADC on GPIO3, Matter Power Source cluster
- Low battery thresholds: warning at 3.5 V, critical at 3.3 V

## Servo Calibration

Adjust these values in [main/servo_lock.cpp](main/servo_lock.cpp) for your lock mechanism:

```cpp
constexpr int kLockedAngle = 15;
constexpr int kUnlockedAngle = 105;
constexpr int kMinPulseUs = 500;
constexpr int kMaxPulseUs = 2500;
```

Current pulse widths:

- Locked: about 666 us
- Unlocked: about 1666 us

## Battery Monitoring

Battery state is exposed through the Matter Power Source cluster on the root endpoint.
The firmware periodically measures the divided battery voltage on GPIO3, updates
`BatVoltage`, `BatPercentRemaining`, `BatChargeLevel`, and sets
`BatReplacementNeeded` when the battery reaches the critical threshold.

The default voltage-to-percent mapping is linear from 3.3 V to 4.2 V for a single
Li-ion/LiPo cell:

- 4.2 V: 100%
- 3.5 V: warning
- 3.3 V: critical / replacement needed

Because this changes the Matter data model, delete and re-pair the device in
Google Home after flashing this firmware if the device was already commissioned.

## Build and Flash

This project uses ESP-IDF v5.4.1 and the `espressif/esp_matter` managed component.

## Prerequisites After Clone

After cloning this repository, run the matching script for your operating system.
The scripts install ESP-IDF v5.4.1 into the local `.espressif` directory, install
the ESP32-C3 toolchain, export the ESP-IDF environment, and set the project target
to `esp32c3`.
ESP-IDF creates and uses its own Python virtual environment under
`.espressif/tools/python_env`; the scripts verify that this venv is active after
exporting the ESP-IDF environment.
After that, the scripts run `idf.py reconfigure` so ESP-IDF Component Manager can
resolve and download managed components such as `espressif/esp_matter` before the
first build.

Windows PowerShell:

```powershell
.\tools\install_prereqs_windows.ps1
```

If Git or Python is missing on Windows, allow the script to install them with
`winget`:

```powershell
.\tools\install_prereqs_windows.ps1 -InstallApps
```

macOS:

```bash
chmod +x tools/install_prereqs_macos.sh
./tools/install_prereqs_macos.sh
```

Linux:

```bash
chmod +x tools/install_prereqs_linux.sh
./tools/install_prereqs_linux.sh
```

For a new terminal session after installation, export the ESP-IDF environment
again before building:

```bash
export IDF_TOOLS_PATH="$PWD/.espressif/tools"
. .espressif/esp-idf-v5.4.1/export.sh
```

On Windows:

```powershell
$env:IDF_TOOLS_PATH = "$PWD\.espressif\tools"
. .\.espressif\esp-idf-v5.4.1\export.ps1
```

```powershell
idf.py set-target esp32c3
idf.py build
idf.py -p COM10 flash monitor
```

## Google Home Pairing

After a fresh flash or NVS erase, the device opens a Matter commissioning window and advertises over BLE as:

```text
MATTER-3840
```

Use Google Home to add a new Matter device. Manual pairing code:

```text
34970112332
```

If Google Home asks for a setup PIN:

```text
20202021
```

## Voice Trigger

After pairing, rename the auxiliary switch endpoint in Google Home to a phrase that Google Assistant will treat as a switch, for example:

```text
Front door button
```

Then say:

```text
Hey Google, turn on Front door button
```

The servo will unlock, wait 5 seconds, and lock again.

## Re-pairing

If Google Home keeps the old device offline after firmware or endpoint changes, delete the old device in Google Home and erase the ESP32-C3 NVS partition:

```powershell
.\tools\erase_nvs.ps1
```

The script defaults to `COM10`. To use another port:

```powershell
.\tools\erase_nvs.ps1 -Port COM7
```

Equivalent raw command:

```powershell
python $env:IDF_PATH\components\esptool_py\esptool\esptool.py --chip esp32c3 -p COM10 erase_region 0x9000 0x6000
```

Reboot the ESP32-C3 and pair it again as a new Matter device.

## Safety

This is a development prototype. Before using it on a real door, review manual override, power-loss behavior, mechanical safety, state sensing, access control, and any Matter certification requirements.

## References

- [Espressif ESP-Matter Programming Guide for ESP32-C3](https://documentation.espressif.com/esp-matter/en/latest/esp32c3/index.html)
- [Espressif ESP-Matter component registry](https://components.espressif.com/components/espressif/esp_matter)
- [ESP-IDF LEDC for ESP32-C3](https://docs.espressif.com/projects/esp-idf/en/latest/esp32c3/api-reference/peripherals/ledc.html)
- [Google Home Matter hub requirements](https://support.google.com/googlenest/answer/12391458)

---

# ESP32-C3 Matter 서보 도어락

ESP32-C3 Super Mini를 Wi-Fi Matter 도어락으로 만들고, RC 서보모터를 PWM으로 구동하는 ESP-IDF 프로젝트입니다.

이 펌웨어는 두 개의 Matter endpoint를 제공합니다.

- Google Home 앱 제어를 위한 표준 Door Lock endpoint
- Google Assistant 음성 트리거로 문을 열기 위한 보조 On/Off Plug-in Unit endpoint

Google Assistant/Nest Mini는 보안상의 이유로 실제 Door Lock 기기의 음성 잠금해제를 차단합니다. 그래서 보조 스위치를 Google Home에서 예를 들어 `현관문 버튼` 같은 이름으로 바꾼 뒤, 순간 열림 트리거로 사용할 수 있습니다. 스위치가 켜지면 서보가 열림 위치로 이동하고, 5초 뒤 자동으로 잠김 위치로 돌아갑니다.

## 하드웨어

| RC Servo | ESP32-C3 Super Mini |
| --- | --- |
| Signal | GPIO4 |
| VCC | 외부 5 V 전원 |
| GND | ESP32-C3와 공통 GND |

| 배터리 모니터 | ESP32-C3 Super Mini |
| --- | --- |
| 분압 중간점 | GPIO3 |
| 분압 상단 | 배터리 + 에서 100 kOhm 경유 |
| 분압 하단 | GND로 100 kOhm 경유 |

서보를 ESP32-C3의 3.3 V 핀에서 직접 구동하지 마세요. SG90 같은 소형 서보도 순간 전류가 커서 보드가 리셋될 수 있습니다.
배터리 모니터는 1셀 Li-ion/LiPo 배터리와 1:1 분압 회로를 기본값으로 가정합니다. 다른 배터리 팩, ADC 핀, 저항 비율을 사용한다면 [main/battery_monitor.cpp](main/battery_monitor.cpp)의 상수를 조정하세요.

## 기능

- 대상 보드: ESP32-C3
- Matter 기기 타입: Door Lock
- 보조 음성 트리거: On/Off Plug-in Unit
- 테스트한 Matter 허브: Google Nest Mini 2세대 + Google Home
- 서보 PWM: LEDC low-speed mode, 50 Hz
- 서보 신호 핀: GPIO4
- 음성 트리거 자동 재잠금 지연: 5초
- 배터리 모니터: GPIO3 ADC, Matter Power Source cluster
- 배터리 부족 기준: 3.5 V에서 경고, 3.3 V에서 심각

## 서보 보정

실제 잠금 장치에 맞게 [main/servo_lock.cpp](main/servo_lock.cpp)의 값을 조정하세요.

```cpp
constexpr int kLockedAngle = 15;
constexpr int kUnlockedAngle = 105;
constexpr int kMinPulseUs = 500;
constexpr int kMaxPulseUs = 2500;
```

현재 펄스 폭은 다음과 같습니다.

- 잠김: 약 666 us
- 열림: 약 1666 us

## 배터리 모니터링

배터리 상태는 루트 endpoint의 Matter Power Source cluster로 노출됩니다.
펌웨어는 GPIO3으로 들어오는 분압된 배터리 전압을 주기적으로 측정하고
`BatVoltage`, `BatPercentRemaining`, `BatChargeLevel`을 갱신합니다.
배터리가 심각 단계에 도달하면 `BatReplacementNeeded`도 켜서 교체 또는 충전이
필요한 상태로 표시합니다.

기본 전압-퍼센트 변환은 1셀 Li-ion/LiPo 기준으로 3.3 V부터 4.2 V까지 선형으로 계산합니다.

- 4.2 V: 100%
- 3.5 V: 경고
- 3.3 V: 심각 / 교체 필요

이 기능은 Matter data model을 바꾸므로, 이미 Google Home에 페어링된 장치라면
펌웨어 업로드 후 기존 장치를 삭제하고 다시 페어링하세요.

## 빌드 및 업로드

이 프로젝트는 ESP-IDF v5.4.1과 `espressif/esp_matter` managed component를 사용합니다.

## 클론 후 사전 준비

이 저장소를 클론한 뒤에는 운영체제에 맞는 설치 스크립트를 실행하세요.
스크립트는 ESP-IDF v5.4.1을 로컬 `.espressif` 디렉터리에 설치하고,
ESP32-C3 툴체인을 설치한 다음 ESP-IDF 환경을 export하고 프로젝트 타깃을
`esp32c3`로 설정합니다.
ESP-IDF는 `.espressif/tools/python_env` 아래에 전용 Python 가상환경을 만들고
사용합니다. 설치 스크립트는 ESP-IDF 환경을 export한 뒤 이 venv가 활성화됐는지
검증합니다.
그 다음 `idf.py reconfigure`를 실행해서 첫 빌드 전에 ESP-IDF Component Manager가
`espressif/esp_matter` 같은 managed component를 해석하고 다운로드하도록 합니다.

Windows PowerShell:

```powershell
.\tools\install_prereqs_windows.ps1
```

Windows에서 Git 또는 Python이 없다면 `winget`으로 설치하도록 허용할 수 있습니다.

```powershell
.\tools\install_prereqs_windows.ps1 -InstallApps
```

macOS:

```bash
chmod +x tools/install_prereqs_macos.sh
./tools/install_prereqs_macos.sh
```

Linux:

```bash
chmod +x tools/install_prereqs_linux.sh
./tools/install_prereqs_linux.sh
```

설치 후 새 터미널을 열었다면 빌드 전에 ESP-IDF 환경을 다시 export하세요.

```bash
export IDF_TOOLS_PATH="$PWD/.espressif/tools"
. .espressif/esp-idf-v5.4.1/export.sh
```

Windows에서는 다음과 같이 실행합니다.

```powershell
$env:IDF_TOOLS_PATH = "$PWD\.espressif\tools"
. .\.espressif\esp-idf-v5.4.1\export.ps1
```

```powershell
idf.py set-target esp32c3
idf.py build
idf.py -p COM10 flash monitor
```

## Google Home 페어링

새로 플래시했거나 NVS를 지운 뒤에는 장치가 Matter commissioning window를 열고 BLE에서 다음 이름으로 광고합니다.

```text
MATTER-3840
```

Google Home에서 새 Matter 기기를 추가하세요. 수동 페어링 코드는 다음과 같습니다.

```text
34970112332
```

Google Home이 setup PIN을 물으면 다음 값을 사용하세요.

```text
20202021
```

## 음성 트리거

페어링 후 Google Home에서 보조 스위치 endpoint의 이름을 Google Assistant가 스위치로 인식할 만한 이름으로 바꾸세요. 예를 들면 다음과 같습니다.

```text
현관문 버튼
```

그 다음 이렇게 말합니다.

```text
Hey Google, 현관문 버튼 켜줘
```

서보가 열림 위치로 이동하고, 5초 대기 후 다시 잠김 위치로 돌아갑니다.

## 재페어링

펌웨어나 endpoint 구성을 바꾼 뒤 Google Home에 예전 장치가 계속 오프라인으로 남아 있다면, Google Home에서 기존 장치를 삭제하고 ESP32-C3의 NVS 파티션을 지우세요.

```powershell
.\tools\erase_nvs.ps1
```

스크립트의 기본 포트는 `COM10`입니다. 다른 포트를 쓰려면 다음처럼 실행합니다.

```powershell
.\tools\erase_nvs.ps1 -Port COM7
```

같은 동작을 하는 원본 명령은 다음과 같습니다.

```powershell
python $env:IDF_PATH\components\esptool_py\esptool\esptool.py --chip esp32c3 -p COM10 erase_region 0x9000 0x6000
```

ESP32-C3를 재부팅한 뒤 새 Matter 기기로 다시 페어링합니다.

## 안전

이 프로젝트는 개발용 프로토타입입니다. 실제 문에 적용하기 전에는 수동 해제 구조, 전원 차단 시 동작, 기계적 안전성, 상태 감지, 접근 제어, Matter 인증 요구사항 등을 검토해야 합니다.

## 참고 문서

- [Espressif ESP-Matter Programming Guide for ESP32-C3](https://documentation.espressif.com/esp-matter/en/latest/esp32c3/index.html)
- [Espressif ESP-Matter component registry](https://components.espressif.com/components/espressif/esp_matter)
- [ESP-IDF LEDC for ESP32-C3](https://docs.espressif.com/projects/esp-idf/en/latest/esp32c3/api-reference/peripherals/ledc.html)
- [Google Home Matter hub requirements](https://support.google.com/googlenest/answer/12391458)
