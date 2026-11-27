[2026-09-18 audio_doorlock 수정본]

기준 하드웨어
- ESP32-C3 Super Mini
- GPIO3: 배터리 ADC (PACK+ -- 100k -- GPIO3 -- 100k -- GND)
- GPIO4: MG90S Signal
- GPIO5: 상태 LED
- GPIO6: Factory Reset 버튼 (GPIO6 -- 버튼 -- GND, 내부 pull-up)
- VLT-CP013 5V OUT: ESP32 5V/VBUS + MG90S VCC
- 모든 GND 공통

반드시 같이 수정할 하드웨어
1) LED가 현재 1kΩ이면 330Ω 직렬저항부터 권장.
   GPIO5 -- 330Ω -- LED -- GND
   펌웨어는 이미 GPIO5를 HIGH 100%로 출력하므로 밝기 문제는 코드로 더 올릴 수 없음.

2) 배터리 ADC
   PACK+ -- 100kΩ --+-- GPIO3
                    |
                  100kΩ
                    |
                   GND
   권장: GPIO3 -- 100nF 세라믹 -- GND 추가.
   정상 1S Li-Po 기준 대략:
   PACK 3.30V -> GPIO3 1.65V
   PACK 4.20V -> GPIO3 2.10V

수정된 소프트웨어 핵심
- GPIO6 100ms 디바운스 + 5초 연속 눌림 판정.
- 부팅 중 버튼이 눌려 있어도 리셋 루프가 생기지 않도록, 안정적인 RELEASE를 먼저 확인한 뒤 arm.
- 리셋은 esp_matter::factory_reset() 사용.
- Factory reset 이후 재부팅하면 fabric 없음 -> GPIO5 0.5초 점멸 + BLE commissioning.
- 5분 commissioning window가 만료되어도 fabric이 없으면 2초 후 다시 open.
- Commissioning session 중에는 중복 window를 열지 않음.
- 런타임에서 마지막 fabric만 제거된 경우 BLE가 이미 종료되었을 수 있으므로 DNS-SD-only 재광고 사용.
- 배터리 ADC 값이 물리적으로 말이 안 되는 범위이면 0%로 속이지 않고 Matter voltage/percent를 NULL로 전송하고 상세 오류 로그 출력.
- 정상 ADC에서는 기존 SOC 테이블로 0~100% 계산.

정상 버튼 로그 순서
1) Factory-reset button ready: GPIO6 active-low, hold for 5 seconds; initial_level=HIGH(released)
2) GPIO6 factory-reset button armed
3) GPIO6 pressed: keep holding for 5 seconds to factory reset
4) GPIO6 held for >=5 seconds: scheduling esp-matter factory reset
5) GPIO6 factory reset confirmed: erasing esp-matter/Matter provisioning and scheduling reboot
6) Factory reset scheduled successfully; device will reboot

배터리 회로 이상 시 예시 로그
Battery measurement INVALID: ... GPIO3=xxxmV ... Check PACK+/PACK-, 100k/100k divider, GPIO3 node and common GND.
Matter battery measurement unavailable: ... voltage/percentage published as NULL

빌드/플래시 예시 (ESP-IDF PowerShell 활성화 후)
cd C:\espwork\audio_doorlock
idf.py fullclean
idf.py build
idf.py -p COM포트 flash monitor

주의
- 이 묶음은 사용자가 올린 최신 소스를 기준으로 수정한 코드이며, 이 환경에는 사용자의 ESP-IDF/esp-matter 빌드 툴체인이 없어 실제 보드 타깃 링크 빌드까지 실행하지는 못했음.
- 첫 빌드에서 컴파일 오류가 생기면 오류 로그 전체를 보내면 현재 esp-matter 1.4.2~1 / ESP-IDF 5.4.1 기준으로 바로 맞춰야 함.
