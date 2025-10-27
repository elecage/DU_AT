from machine import Pin, UART
import time

# --- 핀 설정 ---
POWER_PIN = 7                  # DFPlayer 전원 제어용 GPIO (High=ON, Low=OFF)
TRACK_BUTTON_PINS = [2, 3, 4, 5]  # 트랙/기능 버튼 (1~4번)
STOP_BUTTON_PIN = 6            # 재생 중지 버튼

# --- DFPlayer UART 설정 (UART0, TX=GP0, RX=GP1) ---
uart = UART(0, baudrate=9600, tx=Pin(0), rx=Pin(1))

# --- DFPlayer 전원 제어 핀 ---
power_pin = Pin(POWER_PIN, Pin.OUT)
power_pin.value(0)  # 시작 시 DFPlayer OFF

# --- DFPlayer 명령어 상수 ---
CMD_PLAY_TRACK = 0x03
CMD_STOP       = 0x16
CMD_VOL_UP     = 0x04
CMD_VOL_DOWN   = 0x05

# --- 상태 변수 ---
track_to_play = None
stop_requested = False
playing = False


# === DFPlayer 명령 전송 함수 ===
def send_dfplayer_command(cmd, param1, param2):
    packet = bytearray(10)
    packet[0] = 0x7E
    packet[1] = 0xFF
    packet[2] = 0x06
    packet[3] = cmd
    packet[4] = 0x00
    packet[5] = param1
    packet[6] = param2
    checksum = 0xFFFF - ((packet[1] + packet[2] + packet[3] +
                           packet[4] + packet[5] + packet[6]) & 0xFFFF) + 1
    packet[7] = (checksum >> 8) & 0xFF
    packet[8] = checksum & 0xFF
    packet[9] = 0xEF
    uart.write(packet)
    time.sleep_ms(100)


# === 버튼 인터럽트 핸들러 ===
def make_button_handler(button_index):
    last_press_time = 0

    def handler(pin):
        nonlocal last_press_time
        t = time.ticks_ms()
        if time.ticks_diff(t, last_press_time) < 200:  # 디바운스
            return
        last_press_time = t

        global track_to_play, stop_requested, playing

        if button_index == 5:  # STOP 버튼
            if playing:
                stop_requested = True

        elif button_index == 2:  # 2번: 볼륨 감소 + 트랙 2
            if not playing:
                track_to_play = 2
                send_dfplayer_command(CMD_VOL_DOWN, 0x00, 0x00)

        elif button_index == 3:  # 3번: 볼륨 증가 + 트랙 3
            if not playing:
                track_to_play = 3
                send_dfplayer_command(CMD_VOL_UP, 0x00, 0x00)

        else:
            # 1, 4번 트랙 버튼
            if not playing:
                track_to_play = button_index

    return handler


# --- 버튼 핀 초기화 ---
for i, gp in enumerate(TRACK_BUTTON_PINS, start=1):
    pin = Pin(gp, Pin.IN, Pin.PULL_UP)
    pin.irq(trigger=Pin.IRQ_FALLING, handler=make_button_handler(i))

stop_pin = Pin(STOP_BUTTON_PIN, Pin.IN, Pin.PULL_UP)
stop_pin.irq(trigger=Pin.IRQ_FALLING, handler=make_button_handler(5))


# === DFPlayer 응답 파서 ===
def parse_dfplayer_response(data):
    if len(data) < 10:
        return None, None, None
    if data[0] != 0x7E or data[9] != 0xEF:
        return None, None, None
    cmd = data[3]
    p1 = data[5]
    p2 = data[6]
    return cmd, p1, p2


# === 메인 루프 ===
time.sleep_ms(2000)
print("DFPlayer 시스템 시작")

while True:
    if not playing:
        time.sleep_ms(10)

    if stop_requested:
        if playing:
            send_dfplayer_command(CMD_STOP, 0x00, 0x00)
            time.sleep_ms(100)
        power_pin.value(0)
        playing = False
        stop_requested = False
        track_to_play = None
        continue

    if track_to_play is not None:
        track_num = track_to_play
        track_to_play = None

        # DFPlayer 전원 켜기
        power_pin.value(1)
        time.sleep_ms(300)  # DFPlayer 초기화 대기

        # UART 초기화 (잔여 데이터 비움)
        while uart.any():
            uart.read()

        # 트랙 재생 명령 전송
        send_dfplayer_command(CMD_PLAY_TRACK, (track_num >> 8) & 0xFF, track_num & 0xFF)
        playing = True

        # 재생 완료 감시
        track_finished = False
        while True:
            if stop_requested:
                break
            if uart.any() >= 10:
                resp = uart.read(10)
                cmd, p1, p2 = parse_dfplayer_response(resp)
                if cmd == 0x3D:  # 재생 완료
                    track_finished = True
                    break
            time.sleep_ms(20)

        if stop_requested:
            send_dfplayer_command(CMD_STOP, 0x00, 0x00)
            time.sleep_ms(100)

        power_pin.value(0)
        playing = False
        stop_requested = False

