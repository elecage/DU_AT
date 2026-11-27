
#(13)
import os, sys, math, time, csv, datetime, random
from collections import deque

import pygame
import pygame.gfxdraw

import asyncio, threading
try:
    from bleak import BleakScanner, BleakClient
except ImportError:
    BleakScanner = None
    BleakClient = None

# ===================== 공통 전역 설정 =====================
CH_COUNT = 6

SHOW_HUD          = True
SHOW_HELP         = True
SHOW_FPCB_PANEL   = True
FPCB_PANEL_W_PX   = 420
FPCB_PANEL_GAP_PX = 16
FPCB_IMG_PATH     = "pad.png"
FPCB_MIRROR_X     = False

USE_BLE_TELEMETRY    = True
BLE_DEBUG_PRINT      = True
CLICK_CHANNELS_ENABLED = False
ACTIVE_GUI_MASK        = 0b111100   # Up, Down, Left, Right만 사용
DEVICE_NAME_HINTS    = [
     "한임의",
     "모임의",
     "상임의",
     "김임의",
     "B임의",
     "S임의",
]
LOCK_RELEASE_SEC     = 10.0
STALE_SEC            = 8.0
TOUCH_HOLD_SEC       = 0.12
CACHE_DETECT_SEC     = 6.0
RESTART_COOLDOWN_SEC = 1.0

BLE_CTRL_SERVICE_UUID = "5f6d0001-7f4b-4b2a-8f6c-11d4a1b50001"
BLE_CTRL_CHAR_UUID    = "5f6d0002-7f4b-4b2a-8f6c-11d4a1b50002"
BLE_CTRL_CMD_REINIT_RECAL = 0xA1
BLE_CTRL_CONNECT_TIMEOUT_SEC = 8.0

# BLE 안정화 튜닝
# - active scan만 사용: passive/auto에서 scan response(장치 이름)를 놓쳐 잠금이 끊기는 경우 감소
# - stale/unlock 시간을 늘려 짧은 패킷 공백에 덜 민감하게 함
# - touch hold를 줄여 릴리즈 지연 감소

CAL_DONE_CONFIRM_SEC = 0.60

FPS    = 120
LOG_HZ = 20.0   # 50 ms

APP_MODE_TAPPING = "tapping"
APP_MODE_TASK    = "task"
APP_MODE_MAZE    = "maze"

WINDOWED_W_PX = 1656
WINDOWED_H_PX = 820

COLOR_CAL_PRE_TOUCH = ( 70, 130, 255)
COLOR_TOUCH_RED     = (255,  70,  70)

CAL_STAGE_NAMES = {
    0: "NONE",
    1: "BASELINE",
    2: "WAIT",
    3: "CAPTURE",
    4: "DONE",
    5: "TOUCH_LOST",
    6: "WEAK",
    7: "FAIL",
}

CALIBRATION_ORDER = [2, 3, 4, 5, 0, 1]
CALIBRATION_ORDER_LABELS = ["Up", "Down", "Left", "Right"]

CH_TO_PAD = ["LC", "RC", "UP", "DOWN", "LEFT", "RIGHT"]
MV_LABELS = ["LC", "RC", "UP", "DOWN", "LEFT", "RIGHT"]
LOG_MV_BASE_LABELS = ["LK_mv", "RK_mv", "UP_mv", "DOWN_mv", "LEFT_mv", "RIGHT_mv"]

# 펌웨어 부팅 직후 기본 threshold(1500 mV)가 HT 헤더로 먼저 들어올 수 있음.
# mvToQ_gui(1500) == 1496 이므로, 전 채널 1496은 "캘리 전 기본값"으로 간주한다.
FW_DEFAULT_PRECAL_THR_MV = 1496

PAD_DEFS = {
    "UP":     {"type":"circle", "cx":365.4, "cy":96.2,  "r":63.5},
    "LEFT":   {"type":"circle", "cx":170.1, "cy":281.1, "r":63.5},
    "RIGHT":  {"type":"circle", "cx":560.7, "cy":282.4, "r":63.5},
    "DOWN":   {"type":"circle", "cx":365.4, "cy":463.5, "r":63.5},
    "LC":     {"type":"circle", "cx":194.3, "cy":758.5, "r":63.5},
    "RC":     {"type":"circle", "cx":549.1, "cy":758.5, "r":63.5},
}

PAD_LABEL_TEXT = {
    "UP": "Up",
    "DOWN": "Down",
    "LEFT": "Left",
    "RIGHT": "Right",
}

# ===================== 탭핑/태스크 설정 =====================
AREA_W_MM, AREA_H_MM = 200.0, 200.0
BORDER_MM            = 5.0
MARGIN_MM            = 6.0

EDGE_SPACING_PX    = 400
TARGET_DIAMETER_PX = 52

CURSOR_RADIUS_MM = 1.6
CENTER_HIT_MM    = 2.0

TARGET_COUNT     = 15
SPEED_INIT       = 0.6
VEL_WINDOW       = 0.30
MIN_SPEED        = 5.0

TASK_RELEASE_STABLE_SEC = 0.08
TASK_TOUCH_HOLD_SEC     = 2.0
TASK_TIMEOUT_SEC        = 10.0
TASK_REST_SEC           = 5.0
TASK_FEEDBACK_SEC       = 1.2
TASK_TOTAL_CYCLES       = 10

TASK_STATE_IDLE          = "idle"
TASK_STATE_WAIT_RELEASE  = "wait_release"
TASK_STATE_WAIT_RESPONSE = "wait_response"
TASK_STATE_WAIT_HOLD     = "wait_hold"

COMMAND_LABELS = ["", "", "Up", "Down", "Left", "Right"]
TASK_ITEMS = [
    {"label": "Up",      "prompt": "Move up.",                     "ch": 2},
    {"label": "Down",    "prompt": "Move down.",                   "ch": 3},
    {"label": "Left",    "prompt": "Move left.",                   "ch": 4},
    {"label": "Right",   "prompt": "Move right.",                  "ch": 5},
]

CMD_CODE_LABELS = {
    0: "",
    1: "",
    2: "",
    3: "Up",
    4: "Down",
    5: "Left",
    6: "Right",
    7: "Left-Up",
    8: "Right-Up",
    9: "Left-Down",
    10: "Right-Down",
}

CMD_CODE_TO_MASK = {
    0: 0,
    1: 0,
    2: 0,
    3: 1 << 2,
    4: 1 << 3,
    5: 1 << 4,
    6: 1 << 5,
    7: (1 << 2) | (1 << 4),
    8: (1 << 2) | (1 << 5),
    9: (1 << 3) | (1 << 4),
    10: (1 << 3) | (1 << 5),
}

# ===================== Maze Navigation 설정 =====================
N_PARTICIPANTS       = 5
N_TRIALS_PER_PART    = 3
TRIAL_TIME_LIMIT_SEC = 120.0

MOUSE_GAIN       = 1.0
ARROW_STEP_UNITS = 220.0

PROGRESS_JUMP_MAX_BASE_UNITS = 180.0
PROGRESS_JUMP_PER_GAIN       = 120.0
PROGRESS_BACKTRACK_ALLOW     = 35.0
END_PROGRESS_MARGIN_UNITS    = 35.0

WIDTH_TAPER_UNITS = 80.0
SAMPLE_STEP_UNITS = 6.0
EDGE_PAD_UNITS    = 6.0

LEN_P = 76
LEN_R = 76
LEN_T = 76

# Maze width reduced to about 2/3 of the previous 50-unit width.
W_AG = 34
W_HN = 34
W_OU = 34

COL_BG         = (10, 12, 16)
COL_PANEL_BG   = (18, 22, 28)
COL_TEXT       = (235, 235, 235)
COL_TEXT_DIM   = (170, 170, 170)

COL_TRACK_EDGE = (12, 70, 160)
COL_TRACK_FILL = (40, 130, 230)
COL_TRACK_INNER= (30, 110, 205)
COL_ARROW      = (0, 0, 0)

COL_CURSOR     = (210, 230, 250)
COL_CURSOR_BAD = (255, 80, 80)

COL_A_G = ( 60, 220, 120)
COL_H_N = (255, 190,  80)
COL_O_U = (255,  90,  90)

# Proper ㄹ-shaped maze:
# Start at the upper-left, then go right → down → left → down → right.
# All segments use the same reduced width.
MAZE_STEPS = [
    ("A", 240,   0, 240, W_AG),
    ("B",   0, 120, 120, W_AG),
    ("C",-160,   0, 160, W_AG),
    ("D",   0, 120, 120, W_AG),
    ("E", 280,   0, 280, W_AG),
]

# User monitor reference. Distances shown in the Maze HUD are converted to
# on-screen pixels by multiplying maze-unit distances by maze_scale.
MONITOR_W_PX = 3840
MONITOR_H_PX = 2160
MONITOR_DIAGONAL_IN = 43.0

# ===================== 유틸 =====================
def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v

def lerp(a, b, t):
    return a + (b - a) * t

def resolve_asset(path: str) -> str:
    if os.path.isabs(path):
        return path
    base_dir = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.join(base_dir, path)
    if os.path.exists(cand):
        return cand
    return path

def ble_name_allowed(name) -> bool:
    if not DEVICE_NAME_HINTS:
        return True
    if not name:
        return False

    s = str(name).strip()
    for token in DEVICE_NAME_HINTS:
        if token and (token in s):
            return True
    return False

_FONT_CACHE = {}

def get_font(size:int, bold:bool=False):
    key = (int(size), bool(bold))
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    font_obj = None
    explicit_paths = []
    if sys.platform.startswith("win"):
        explicit_paths += [
            "C:/Windows/Fonts/malgunbd.ttf" if bold else "C:/Windows/Fonts/malgun.ttf",
            "C:/Windows/Fonts/malgun.ttf",
            "C:/Windows/Fonts/gulim.ttc",
            "C:/Windows/Fonts/batang.ttc",
            "C:/Windows/Fonts/arialuni.ttf",
        ]
    elif sys.platform == "darwin":
        explicit_paths += [
            "/System/Library/Fonts/AppleSDGothicNeo.ttc",
            "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        ]
    else:
        explicit_paths += [
            "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf" if bold else "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
            "/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf" if bold else "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        ]

    for fp in explicit_paths:
        try:
            if fp and os.path.exists(fp):
                font_obj = pygame.font.Font(fp, size)
                break
        except Exception:
            pass

    if font_obj is None:
        candidates = [
            "malgungothic", "Malgun Gothic", "맑은 고딕",
            "NanumGothic", "Nanum Gothic", "나눔고딕",
            "NanumBarunGothic", "나눔바른고딕",
            "Noto Sans CJK KR", "Noto Sans KR",
            "Apple SD Gothic Neo", "AppleGothic",
            "Arial Unicode MS",
        ]
        for name in candidates:
            try:
                font_path = pygame.font.match_font(name, bold=bold)
                if font_path:
                    font_obj = pygame.font.Font(font_path, size)
                    break
            except Exception:
                pass

    if font_obj is None:
        font_obj = pygame.font.SysFont(None, size, bold=bold)

    _FONT_CACHE[key] = font_obj
    return font_obj

def screen_heading_deg(vx, vy):
    return (math.degrees(math.atan2(-vy, vx)) + 360.0) % 360.0

def build_labels_ring_like_screenshot(n:int):
    special = f"0/{n}"
    max_even = n-1 if (n-1) % 2 == 0 else n-2
    max_odd  = n-2 if (n-2) % 2 == 1 else n-3
    evens = [str(i) for i in range(max_even, 1, -2)]
    odds  = [str(i) for i in range(max_odd, 2, -2)]
    labels = ["1"] + evens + [special] + odds
    if len(labels) != n:
        labels = [special] + [str(i) for i in range(1, n)]
    return labels

def bit_count(v: int) -> int:
    return int(v).bit_count()

def mask_to_command(mask: int) -> str:
    labels = [COMMAND_LABELS[ch] for ch in range(CH_COUNT) if mask & (1 << ch)]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    return "+".join(labels)

def choose_next_task(prev_idx=None):
    choices = list(range(len(TASK_ITEMS)))
    if prev_idx is not None and len(choices) > 1 and prev_idx in choices:
        choices.remove(prev_idx)
    return random.choice(choices)

def build_task_sequence(total_cycles: int = TASK_TOTAL_CYCLES):
    seq = []
    base = list(range(len(TASK_ITEMS)))
    for _ in range(total_cycles):
        block = base[:]
        random.shuffle(block)
        seq.extend(block)
    return seq

def compute_speed_heading_once(hist: deque, x: float, y: float, vel_window: float, min_speed: float):
    now = time.perf_counter()
    hist.append((now, x, y))
    while hist and (now - hist[0][0] > vel_window):
        hist.popleft()
    if len(hist) < 2:
        return 0.0, None
    t0, x0, y0 = hist[0]
    t1, x1, y1 = hist[-1]
    dt = max(1e-6, t1 - t0)
    vx = (x1 - x0) / dt
    vy = (y1 - y0) / dt
    spd = math.hypot(vx, vy)
    if spd < min_speed:
        return spd, None
    return spd, screen_heading_deg(vx, vy)

# ===================== HM payload 파서 =====================
def parse_hm_payload(payload_like):
    if not payload_like:
        return None
    p = bytes(payload_like)

    if len(p) >= 4 and p[0] == 0xFF and p[1] == 0xFF and p[2:4] == b"HM":
        p = p[2:]

    if len(p) < 3 or p[0:2] != b"HM":
        return None

    ver = p[2]

    if ver == 4 and len(p) >= 20:
        seq = int(p[3] | (p[4] << 8))
        raw_mask = int(p[5] & 0x3F)
        cmd_mask = int(p[6] & 0x3F)
        flags = int(p[7])
        inCal = bool(flags & 0x01)
        hid_connected = bool(flags & 0x02)
        all_valid = bool(flags & 0x04)
        wet_lock = bool(flags & 0x08)
        cal_ch = int(p[8])
        stage = int(p[9])
        cmd_code = int(p[10])
        cal_quality_q = int(p[11])
        mvq = p[12:18]
        ratioq = p[18:24] if len(p) >= 24 else bytes([0] * CH_COUNT)
        mv6 = [int(q) * 11 for q in mvq]
        ratio6 = [int(q) for q in ratioq[:CH_COUNT]]

        if not (cal_ch == 255 or 0 <= cal_ch <= (CH_COUNT - 1)):
            cal_ch = 255
        if stage > 20:
            stage = 0

        return {
            "ver": 4,
            "seq": seq,
            "mask": cmd_mask,
            "raw_mask": raw_mask,
            "cmd_mask": cmd_mask,
            "inCal": inCal,
            "hid_connected": hid_connected,
            "all_valid": all_valid,
            "wet_lock": wet_lock,
            "cal_ch": cal_ch,
            "stage": stage,
            "cmd_code": cmd_code,
            "cal_quality_q": cal_quality_q,
            "mv": mv6,
            "ratio": ratio6,
        }

    if ver == 3 and len(p) >= 15:
        seq = int(p[3] | (p[4] << 8))
        mask = int(p[5] & 0x3F)
        flags = int(p[6])
        inCal = bool(flags & 0x01)
        cal_ch = int(p[7])
        stage = int(p[8])
        mvq = p[9:15]
        mv6 = [int(q) * 11 for q in mvq]

        if not (cal_ch == 255 or 0 <= cal_ch <= (CH_COUNT-1)):
            cal_ch = 255
        if stage > 20:
            stage = 0

        return {
            "ver": 3,
            "seq": seq,
            "mask": mask,
            "raw_mask": mask,
            "cmd_mask": mask,
            "inCal": inCal,
            "hid_connected": False,
            "all_valid": True,
            "cal_ch": cal_ch,
            "stage": stage,
            "cmd_code": 0,
            "cal_quality_q": 0,
            "mv": mv6,
            "ratio": [0] * CH_COUNT,
        }

    if ver == 2 and len(p) >= 13:
        mask   = p[3] & 0x3F
        inCal  = (p[4] == 1)
        cal_ch = p[5]
        stage  = p[6]
        mvq    = p[7:13]
        mv6    = [int(q) * 11 for q in mvq]
        return {
            "ver": 2,
            "seq": None,
            "mask": int(mask),
            "raw_mask": int(mask),
            "cmd_mask": int(mask),
            "inCal": bool(inCal),
            "hid_connected": False,
            "all_valid": True,
            "cal_ch": int(cal_ch),
            "stage": int(stage),
            "cmd_code": 0,
            "cal_quality_q": 0,
            "mv": mv6,
            "ratio": [0] * CH_COUNT,
        }

    return None

def parse_thr_payload(payload_like):
    if not payload_like:
        return None
    p = bytes(payload_like)

    if len(p) >= 4 and p[0] == 0xFE and p[1] == 0xFF and p[2:4] == b"HT":
        p = p[2:]

    if len(p) < 3 or p[0:2] != b"HT":
        return None

    ver = p[2]
    if ver == 2 and len(p) >= 13:
        thrq = p[3:9]
        thr6 = [int(q) * 11 for q in thrq[:CH_COUNT]]
        battery_mv = int(p[9] | (p[10] << 8))
        battery_pct = int(p[11])
        flags = int(p[12])
        return {
            "ver": 2,
            "thr_mv": thr6,
            "battery_mv": battery_mv,
            "battery_pct": battery_pct,
            "battery_valid": bool(flags & 0x01),
        }

    if ver == 1 and len(p) >= 9:
        thrq = p[3:9]
        thr6 = [int(q) * 11 for q in thrq[:CH_COUNT]]
        return {
            "ver": 1,
            "thr_mv": thr6,
        }

    return None

# ===================== Maze helper =====================
def build_maze_segments():
    x, y = 0.0, 0.0
    V = [(x, y)]
    segs = []
    total_L_label = 0.0

    for (name, dx, dy, L_label, Wg) in MAZE_STEPS:
        x2, y2 = x + float(dx), y + float(dy)
        p0 = (x, y)
        p1 = (x2, y2)
        gl = math.hypot(x2-x, y2-y)
        segs.append({"name":name,"p0":p0,"p1":p1,"L":float(L_label),"Wg":float(Wg),"geom_len":gl})
        V.append((x2, y2))
        x, y = x2, y2
        total_L_label += float(L_label)

    IDp = 0.0
    for s in segs:
        IDp += math.log2(s["L"] / s["Wg"] + 1.0)

    xs = [p[0] for p in V]
    ys = [p[1] for p in V]
    pad = max(W_AG, W_HN, W_OU) * 1.2
    bounds = (min(xs)-pad, min(ys)-pad, max(xs)+pad, max(ys)+pad)
    return V, segs, total_L_label, IDp, bounds

def dist_point_to_segment(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    vv = vx*vx + vy*vy
    if vv <= 1e-12:
        return math.hypot(px-ax, py-ay), 0.0
    t = (wx*vx + wy*vy) / vv
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    cx, cy = ax + t*vx, ay + t*vy
    return math.hypot(px-cx, py-cy), t

def compute_vertex_widths_with_taper(V, segs, taper_units):
    seg_len = [math.hypot(V[i+1][0]-V[i][0], V[i+1][1]-V[i][1]) for i in range(len(V)-1)]
    s_at_v = [0.0]
    for gl in seg_len:
        s_at_v.append(s_at_v[-1] + gl)

    boundaries = []
    for i in range(1, len(segs)):
        w0 = segs[i-1]["Wg"]; w1 = segs[i]["Wg"]
        if abs(w1 - w0) > 1e-6:
            boundaries.append((s_at_v[i], w0, w1))

    def w_of_s(s):
        idx = 0
        for k in range(len(segs)):
            if s <= s_at_v[k+1]:
                idx = k
                break
            idx = len(segs)-1
        w = segs[idx]["Wg"]

        for (sb, w0, w1) in boundaries:
            a = sb - taper_units*0.5
            b = sb + taper_units*0.5
            if (s < a) or (s > b):
                continue
            t = (s - a) / max(1e-6, (b - a))
            w = lerp(w0, w1, t)
        return w

    Wv = [w_of_s(s) for s in s_at_v]
    return Wv, s_at_v, seg_len

def sample_centerline(V, Wv, step_units):
    seg_len = [math.hypot(V[i+1][0]-V[i][0], V[i+1][1]-V[i][1]) for i in range(len(V)-1)]
    s_at_v = [0.0]
    for gl in seg_len:
        s_at_v.append(s_at_v[-1] + gl)
    total = s_at_v[-1]

    P=[]; T=[]; W=[]; S=[]
    meta_mid = []
    if total <= 1e-9:
        return P,T,W,S,meta_mid

    s = 0.0
    cur_seg = 0
    while s <= total + 1e-6:
        while (cur_seg < len(seg_len)-1) and (s > s_at_v[cur_seg+1]):
            cur_seg += 1
        aS = s_at_v[cur_seg]; bS = s_at_v[cur_seg+1]
        tt = (s - aS) / max(1e-6, (bS - aS))

        x = lerp(V[cur_seg][0], V[cur_seg+1][0], tt)
        y = lerp(V[cur_seg][1], V[cur_seg+1][1], tt)

        vx = V[cur_seg+1][0]-V[cur_seg][0]
        vy = V[cur_seg+1][1]-V[cur_seg][1]
        vd = math.hypot(vx,vy)
        tx,ty = (vx/vd, vy/vd) if vd > 1e-9 else (1.0,0.0)

        ww = lerp(Wv[cur_seg], Wv[cur_seg+1], tt)

        P.append((x,y)); T.append((tx,ty)); W.append(ww); S.append(s)
        s += step_units

    for i in range(len(seg_len)):
        midS = (s_at_v[i] + s_at_v[i+1]) * 0.5
        tt = (midS - s_at_v[i]) / max(1e-6, seg_len[i])
        mx = lerp(V[i][0], V[i+1][0], tt)
        my = lerp(V[i][1], V[i+1][1], tt)
        vx = V[i+1][0]-V[i][0]; vy = V[i+1][1]-V[i][1]
        vd = math.hypot(vx,vy)
        tx,ty = (vx/vd, vy/vd) if vd > 1e-9 else (1.0,0.0)
        meta_mid.append((i, (mx,my), (tx,ty)))

    return P,T,W,S,meta_mid

def build_track_polygons(P, T, W, edge_pad_units):
    left_edge=[]; right_edge=[]
    left_fill=[]; right_fill=[]
    left_inner=[]; right_inner=[]

    for (p,(tx,ty),ww) in zip(P,T,W):
        nx, ny = -ty, tx
        hw_fill = ww * 0.5
        hw_edge = hw_fill + edge_pad_units
        hw_inner= max(0.0, hw_fill * 0.72)

        left_edge.append((p[0] + nx*hw_edge, p[1] + ny*hw_edge))
        right_edge.append((p[0] - nx*hw_edge, p[1] - ny*hw_edge))

        left_fill.append((p[0] + nx*hw_fill, p[1] + ny*hw_fill))
        right_fill.append((p[0] - nx*hw_fill, p[1] - ny*hw_fill))

        left_inner.append((p[0] + nx*hw_inner, p[1] + ny*hw_inner))
        right_inner.append((p[0] - nx*hw_inner, p[1] - ny*hw_inner))

    poly_edge  = left_edge + list(reversed(right_edge))
    poly_fill  = left_fill + list(reversed(right_fill))
    poly_inner = left_inner + list(reversed(right_inner))
    return {"edge": poly_edge, "fill": poly_fill, "inner": poly_inner}

def point_inside_variable_width(x, y, V, Wv):
    for i in range(len(V)-1):
        ax,ay = V[i]; bx,by = V[i+1]
        d,t = dist_point_to_segment(x,y,ax,ay,bx,by)
        w = lerp(Wv[i], Wv[i+1], t)
        if d <= (w*0.5):
            return True
    return False

def closest_s_on_polyline(x, y, V, Wv, seg_len, s_at_v):
    best = None
    for i in range(len(V)-1):
        ax,ay = V[i]; bx,by = V[i+1]
        d,t = dist_point_to_segment(x,y,ax,ay,bx,by)
        w = lerp(Wv[i], Wv[i+1], t)
        s = s_at_v[i] + t * seg_len[i]
        if (best is None) or (d < best[0]):
            best = (d, i, t, s, w)
    return best

def accumulate_in_out(p0, p1, V, Wv):
    x0,y0 = p0; x1,y1 = p1
    d = math.hypot(x1-x0, y1-y0)
    if d <= 1e-9:
        return 0.0, 0.0
    step = 10.0
    n = int(math.ceil(d/step))
    n = max(1, min(n, 40))

    inside = 0.0
    outside = 0.0
    prev = (x0,y0)
    for i in range(1, n+1):
        t = i / n
        cur = (x0 + (x1-x0)*t, y0 + (y1-y0)*t)
        dd = math.hypot(cur[0]-prev[0], cur[1]-prev[1])
        mid = ((cur[0]+prev[0])*0.5, (cur[1]+prev[1])*0.5)
        if point_inside_variable_width(mid[0], mid[1], V, Wv):
            inside += dd
        else:
            outside += dd
        prev = cur
    return inside, outside


def segment_inside_variable_width(p0, p1, V, Wv, step_units=2.0):
    """Return True when the straight segment p0->p1 stays inside the maze track.

    This is used for the visibility-graph shortest path. Sampling is dense enough
    for the current orthogonal ㄹ maze, and it follows the same inside/outside
    definition used by the runtime distance accumulator.
    """
    x0, y0 = p0
    x1, y1 = p1
    d = math.hypot(x1 - x0, y1 - y0)
    if d <= 1e-9:
        return point_inside_variable_width(x0, y0, V, Wv)

    n = max(1, int(math.ceil(d / max(0.5, float(step_units)))))
    for i in range(n + 1):
        t = i / n
        x = x0 + (x1 - x0) * t
        y = y0 + (y1 - y0) * t
        if not point_inside_variable_width(x, y, V, Wv):
            return False
    return True


def compute_shortest_path_inside_maze(V, Wv, sample_step_units=6.0, visibility_step_units=2.0):
    """Compute path^opt as the shortest legal start-to-end path inside the maze.

    The old value, sum of centerline segments, is not the true shortest path.
    Here path^opt is computed once with a visibility graph:
      - nodes: start, end, and the walkable-track boundary sample points
      - edges: straight segments that remain inside the variable-width maze
      - solver: Dijkstra shortest path

    The returned distance is in the maze's internal coordinate units. HUD converts
    it to pixels by multiplying by maze_scale.
    """
    if len(V) < 2:
        return 0.0, []

    start = (float(V[0][0]), float(V[0][1]))
    end = (float(V[-1][0]), float(V[-1][1]))

    P, T, W, S, _ = sample_centerline(V, Wv, max(1.0, float(sample_step_units)))
    polys = build_track_polygons(P, T, W, 0.0)
    boundary = list(polys.get("fill", []))

    nodes = []
    seen = set()

    def add_node(pt):
        key = (round(float(pt[0]), 6), round(float(pt[1]), 6))
        if key in seen:
            return
        seen.add(key)
        nodes.append((float(pt[0]), float(pt[1])))

    add_node(start)
    add_node(end)
    for pt in boundary:
        add_node(pt)

    n_nodes = len(nodes)
    if n_nodes < 2:
        return 0.0, []

    adj = [[] for _ in range(n_nodes)]
    for i in range(n_nodes):
        pi = nodes[i]
        for j in range(i + 1, n_nodes):
            pj = nodes[j]
            if segment_inside_variable_width(pi, pj, V, Wv, visibility_step_units):
                w = math.hypot(pj[0] - pi[0], pj[1] - pi[1])
                adj[i].append((j, w))
                adj[j].append((i, w))

    import heapq
    dist = [float("inf")] * n_nodes
    prev = [-1] * n_nodes
    dist[0] = 0.0
    pq = [(0.0, 0)]

    while pq:
        du, u = heapq.heappop(pq)
        if du != dist[u]:
            continue
        if u == 1:
            break
        for v, w in adj[u]:
            nd = du + w
            if nd < dist[v]:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))

    if not math.isfinite(dist[1]):
        # Fallback: never use this unless visibility graph fails.
        center_len = 0.0
        for i in range(len(V) - 1):
            center_len += math.hypot(V[i+1][0] - V[i][0], V[i+1][1] - V[i][1])
        return center_len, [start, end]

    path = []
    cur = 1
    while cur >= 0:
        path.append(nodes[cur])
        cur = prev[cur]
    path.reverse()
    return dist[1], path

# ===================== 메인 =====================
def main():
    global SHOW_HUD, SHOW_HELP, SHOW_FPCB_PANEL, MOUSE_GAIN

    pygame.init()
    display_info = pygame.display.Info()
    windowed_size = (
        min(WINDOWED_W_PX, max(960, display_info.current_w - 80)),
        min(WINDOWED_H_PX, max(640, display_info.current_h - 80)),
    )
    fullscreen = True
    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    win_w, win_h = screen.get_size()
    clock = pygame.time.Clock()

    font_hud   = get_font(20)
    font_med   = get_font(22, bold=True)
    font_small = get_font(16)
    font_len   = get_font(16, bold=True)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    LOG_DIR = os.path.join(base_dir, "logs")

    # ===== 공통 상태 =====
    app_mode = APP_MODE_TAPPING
    ui_buttons = {}

    calibration_required = True
    calibration_done = False
    calibration_armed = False

    session_device_addr = None
    last_seen_seq = None
    prev_ble_inCal = False
    ready_none_since = None

    status_text = "Click the Calibration button at the top before starting."
    status_until = time.perf_counter() + 4.0

    sim_touch_mask = 0

    # ===== 탭핑/태스크 상태 =====
    tap_px_per_mm = 1.0
    tap_mm_per_px = 1.0
    tap_border_px = 2
    tap_inner_side = 600

    inner = pygame.Rect(0, 0, 100, 100)
    fpcb_rect = pygame.Rect(0, 0, 0, 0)
    control_rect = pygame.Rect(0, 0, 0, 0)

    target_r_px = max(2, TARGET_DIAMETER_PX // 2)
    target_r_mm = 5.0
    ring_r_mm   = 50.0
    cursor_r_px = 3

    labels_ring = build_labels_ring_like_screenshot(TARGET_COUNT)
    special_lbl = f"0/{TARGET_COUNT}"
    seq_labels  = [special_lbl] + [str(i) for i in range(1, TARGET_COUNT)]
    seq_indices = [labels_ring.index(lbl) for lbl in seq_labels]
    targets_mm = [(0.0, 0.0)] * TARGET_COUNT

    seq_pos = 0
    tap_speed = SPEED_INIT
    x_mm, y_mm = AREA_W_MM / 2.0, AREA_H_MM / 2.0

    prev_hit_time = None
    mt_sum = 0.0
    mt_count = 0
    mt_avg_s = None
    tap_pos_hist = deque()

    # Pointing Test Path Efficiency(%)
    # PE = Pathopt / Pathsub * 100
    # Pathopt: target appeared position -> target center, Euclidean distance
    # Pathsub: actual cursor trajectory length accumulated until target hit
    tap_trial_start_x_mm = x_mm
    tap_trial_start_y_mm = y_mm
    tap_path_sub_mm = 0.0
    tap_pe_last_pct = None
    tap_pe_sum = 0.0
    tap_pe_count = 0

    id_act_bits = None

    task_current_idx = None
    task_last_idx = None
    task_feedback = ""
    task_feedback_color = (50, 50, 50)
    task_feedback_until = 0.0
    task_next_prompt_at = 0.0
    task_total = 0
    task_correct = 0
    task_state = TASK_STATE_IDLE
    task_release_zero_since = None
    task_response_deadline = 0.0
    task_prev_input_mask = 0
    task_sequence = []
    task_sequence_pos = 0
    task_candidate_mask = 0
    task_hold_start = 0.0

    pending_response_log = None

    # ===== Maze 상태 =====
    V, maze_segs, maze_total_L_label, maze_IDp, maze_bounds = build_maze_segments()
    maze_Wv, maze_s_at_v, maze_seg_len = compute_vertex_widths_with_taper(V, maze_segs, WIDTH_TAPER_UNITS)
    maze_total_geom_len = maze_s_at_v[-1]
    maze_path_opt_units, maze_path_opt_polyline = compute_shortest_path_inside_maze(
        V, maze_Wv, sample_step_units=3.0, visibility_step_units=2.0
    )
    print(f"[MAZE] path^opt={maze_path_opt_units:.3f} maze-units  centerline={maze_total_geom_len:.3f} maze-units")

    maze_scale = 1.0
    maze_offx  = 0.0
    maze_offy  = 0.0

    maze_polys_px = None
    maze_arrows_px = []
    maze_label_px = []
    maze_len_px   = []
    maze_start_px = (0, 0)
    maze_end_px   = (0, 0)

    participant = 0
    trial = 0
    maze_status = "IDLE"
    trial_t0 = None
    trial_ct = 0.0
    dist_in = 0.0
    dist_out = 0.0

    # Maze Navigation Path Efficiency(%)
    # PE is not calculated continuously during an unfinished maze run.
    # It is finalized only when the maze trial is completed with DONE,
    # because Pathsub must represent the complete traversed path.
    maze_pe_last_pct = None
    maze_pe_sum = 0.0
    maze_pe_count = 0

    maze_cursor = [V[0][0], V[0][1]]
    maze_last_pos = (maze_cursor[0], maze_cursor[1])
    progress_s = 0.0
    results = [[None for _ in range(N_TRIALS_PER_PART)] for __ in range(N_PARTICIPANTS)]
    maze_pos_hist = deque()

    # ===== FPCB 이미지 =====
    fpcb_img_orig = None
    fpcb_img_scaled = None
    fpcb_scale = 1.0
    fpcb_ox = fpcb_oy = 0
    fpcb_w0 = fpcb_h0 = 1

    # ===== 단일 GUI 커서 =====
    gui_cursor_px = win_w // 2
    gui_cursor_py = win_h // 2

    def apply_display_mode():
        nonlocal screen, win_w, win_h, gui_cursor_px, gui_cursor_py
        flags = pygame.FULLSCREEN if fullscreen else pygame.RESIZABLE
        size = (0, 0) if fullscreen else windowed_size
        screen = pygame.display.set_mode(size, flags)
        win_w, win_h = screen.get_size()
        pygame.event.set_grab(False)
        pygame.mouse.set_visible(False)
        rebuild_layout()
        mx, my = pygame.mouse.get_pos()
        gui_cursor_px = int(clamp(mx, 0, win_w - 1))
        gui_cursor_py = int(clamp(my, 0, win_h - 1))
        set_caption()

    def load_fpcb():
        nonlocal fpcb_img_orig, fpcb_w0, fpcb_h0
        path = resolve_asset(FPCB_IMG_PATH)
        try:
            if os.path.exists(path):
                fpcb_img_orig = pygame.image.load(path).convert_alpha()
                if FPCB_MIRROR_X:
                    fpcb_img_orig = pygame.transform.flip(fpcb_img_orig, True, False)
                fpcb_w0, fpcb_h0 = fpcb_img_orig.get_size()
            else:
                fpcb_img_orig = None
        except Exception:
            fpcb_img_orig = None

    load_fpcb()

    # ===== BLE 상태 =====
    ble_lock = threading.Lock()
    ble_state = {
        "t": 0.0,
        "addr": None,
        "name": None,
        "seq": None,
        "mask": 0,
        "raw_mask": 0,
        "cmd_mask": 0,
        "cmd_code": 0,
        "cal_quality_q": 0,
        "ratio": [0] * CH_COUNT,
        "hid_connected": False,
        "all_valid": False,
        "wet_lock": False,
        "inCal": False,
        "cal_ch": 255,
        "stage": 0,
        "mv": None,
        "thr_mv": [None] * CH_COUNT,          # last seen HT payload
        "thr_mv_good": [None] * CH_COUNT,     # last trustworthy HT payload
        "thr_mv_seen_t": 0.0,
        "thr_mv_good_t": 0.0,
        "battery_mv": None,
        "battery_pct": None,
        "battery_valid": False,
        "battery_seen_t": 0.0,
        "last_valid_t": 0.0,
        "last_seq_t": 0.0,
        "restart_req": False,
        "restart_reason": "",
        "last_restart_t": 0.0,
    }
    target_lock = {"addr": None, "name": None, "last_valid_t": 0.0}
    allowed_addrs = set()

    gui_recal_request_pending = False
    gui_recal_started = False

    def normalize_thr_mv_list(thr_mv):
        thr_list = list(thr_mv)[:CH_COUNT] if isinstance(thr_mv, list) else [None] * CH_COUNT
        if len(thr_list) < CH_COUNT:
            thr_list += [None] * (CH_COUNT - len(thr_list))
        out = []
        for v in thr_list[:CH_COUNT]:
            if isinstance(v, (int, float)):
                out.append(int(v))
            else:
                out.append(None)
        return out

    def thr_mv_all_valid(thr_mv):
        thr_list = normalize_thr_mv_list(thr_mv)
        return all(v is not None for v in thr_list)

    def thr_mv_is_default_precal(thr_mv):
        thr_list = normalize_thr_mv_list(thr_mv)
        return thr_mv_all_valid(thr_list) and all(v == FW_DEFAULT_PRECAL_THR_MV for v in thr_list)

    def thr_mv_is_meaningful(thr_mv):
        thr_list = normalize_thr_mv_list(thr_mv)
        return thr_mv_all_valid(thr_list) and (not thr_mv_is_default_precal(thr_list))

    def get_best_thr_mv_snapshot():
        with ble_lock:
            good_thr = normalize_thr_mv_list(ble_state.get("thr_mv_good", [None] * CH_COUNT))
            cur_thr  = normalize_thr_mv_list(ble_state.get("thr_mv", [None] * CH_COUNT))

        if thr_mv_is_meaningful(good_thr):
            return good_thr
        if thr_mv_is_meaningful(cur_thr):
            return cur_thr
        if thr_mv_all_valid(good_thr):
            return good_thr
        if thr_mv_all_valid(cur_thr):
            return cur_thr
        return [None] * CH_COUNT

    def battery_text(battery_mv, battery_pct, battery_valid):
        if battery_valid and isinstance(battery_mv, (int, float)) and battery_mv > 0:
            if isinstance(battery_pct, (int, float)):
                return f"{battery_mv / 1000.0:.2f} V ({int(battery_pct)}%)"
            return f"{battery_mv / 1000.0:.2f} V"
        if isinstance(battery_mv, (int, float)) and battery_mv > 0:
            return f"N/A raw={battery_mv / 1000.0:.2f} V"
        return "N/A"

    def unlock():
        target_lock["addr"] = None
        target_lock["name"] = None
        target_lock["last_valid_t"] = 0.0
        print("[BLE] UNLOCK")

    def set_status(msg: str, sec: float = 1.5):
        nonlocal status_text, status_until
        status_text = msg
        status_until = time.perf_counter() + sec
        print(f"[STATUS] {msg}")

    def reset_for_new_session(reason="New session detected: calibration is required again."):
        nonlocal calibration_required, calibration_done, calibration_armed
        nonlocal session_device_addr, last_seen_seq, prev_ble_inCal, ready_none_since
        nonlocal task_current_idx, task_last_idx, task_feedback, task_feedback_until
        nonlocal task_state, task_release_zero_since, task_response_deadline, task_prev_input_mask
        nonlocal task_sequence, task_sequence_pos, task_candidate_mask, task_hold_start
        nonlocal pending_response_log
        nonlocal gui_recal_request_pending, gui_recal_started

        calibration_required = True
        calibration_done = False
        calibration_armed = False

        with ble_lock:
            ble_state["thr_mv"] = [None] * CH_COUNT
            ble_state["thr_mv_good"] = [None] * CH_COUNT
            ble_state["thr_mv_seen_t"] = 0.0
            ble_state["thr_mv_good_t"] = 0.0
            ble_state["battery_mv"] = None
            ble_state["battery_pct"] = None
            ble_state["battery_valid"] = False
            ble_state["battery_seen_t"] = 0.0

        session_device_addr = None
        last_seen_seq = None
        prev_ble_inCal = False
        ready_none_since = None
        gui_recal_request_pending = False
        gui_recal_started = False

        task_current_idx = None
        task_last_idx = None
        task_feedback = ""
        task_feedback_until = 0.0
        task_state = TASK_STATE_IDLE
        task_release_zero_since = None
        task_response_deadline = 0.0
        task_prev_input_mask = 0
        task_sequence = []
        task_sequence_pos = 0
        task_candidate_mask = 0
        task_hold_start = 0.0
        pending_response_log = None

        set_status(reason, 2.0)

    def mark_calibration_done(dev_addr=None, seq=None):
        nonlocal calibration_required, calibration_done, calibration_armed
        nonlocal session_device_addr, last_seen_seq
        nonlocal gui_recal_request_pending, gui_recal_started
        calibration_required = False
        calibration_done = True
        calibration_armed = False
        gui_recal_request_pending = False
        gui_recal_started = False
        session_device_addr = dev_addr
        last_seen_seq = seq
        set_status("Calibration completed", 1.5)

    def request_calibration():
        nonlocal calibration_armed
        with ble_lock:
            addr = ble_state.get("addr", None)
            ble_ok_local = (time.perf_counter() - float(ble_state.get("last_valid_t", 0.0) or 0.0)) <= STALE_SEC
            in_cal = bool(ble_state.get("inCal", False))

        if calibration_done:
            set_status("Calibration completed", 1.2)
            return

        calibration_armed = True

        if addr is None or (not ble_ok_local):
            set_status("Waiting for board connection...", 1.5)
            return

        if in_cal:
            set_status("Calibration in progress...", 1.5)
        else:
            set_status("Checking calibration state...", 1.5)

    def send_ble_command_once(addr: str, payload: bytes, ok_message: str, fail_prefix: str):
        nonlocal gui_recal_request_pending, gui_recal_started

        if BleakClient is None:
            set_status("BleakClient not available. Install bleak first.", 2.5)
            return

        def worker():
            nonlocal gui_recal_request_pending, gui_recal_started
            try:
                with ble_lock:
                    ble_state["restart_req"] = True
                    ble_state["restart_reason"] = "gatt_write_start"
                time.sleep(0.45)

                async def do_write():
                    client_kwargs = {"timeout": BLE_CTRL_CONNECT_TIMEOUT_SEC}
                    if sys.platform.startswith("win"):
                        client_kwargs["winrt"] = {"use_cached_services": False}

                    client = BleakClient(addr, **client_kwargs)
                    await client.connect()
                    try:
                        # bleak 버전별 API 차이로 get_services()/services/get_characteristic()
                        # 경로에서 AttributeError가 날 수 있으므로, UUID 문자열로 직접 쓰기를 우선 시도한다.
                        wrote = False
                        last_exc = None

                        for use_response in (True, False):
                            try:
                                await client.write_gatt_char(BLE_CTRL_CHAR_UUID, payload, response=use_response)
                                wrote = True
                                break
                            except Exception as exc:
                                last_exc = exc

                        if not wrote:
                            # 일부 bleak/backend 조합은 문자열 UUID 직접쓰기가 실패하므로,
                            # 가능한 경우에만 services 컬렉션을 이용해 재시도한다.
                            services = None
                            get_services_fn = getattr(client, "get_services", None)
                            if callable(get_services_fn):
                                try:
                                    services = await get_services_fn()
                                except Exception:
                                    services = None
                            if services is None:
                                services = getattr(client, "services", None)

                            char_obj = None
                            if services is not None:
                                get_char_fn = getattr(services, "get_characteristic", None)
                                if callable(get_char_fn):
                                    try:
                                        char_obj = get_char_fn(BLE_CTRL_CHAR_UUID)
                                    except Exception:
                                        char_obj = None

                                if char_obj is None:
                                    try:
                                        iter_services = list(services)
                                    except Exception:
                                        iter_services = []
                                    for service in iter_services:
                                        if str(getattr(service, "uuid", "")).lower() != BLE_CTRL_SERVICE_UUID.lower():
                                            continue
                                        for ch in getattr(service, "characteristics", []):
                                            if str(getattr(ch, "uuid", "")).lower() == BLE_CTRL_CHAR_UUID.lower():
                                                char_obj = ch
                                                break
                                        if char_obj is not None:
                                            break

                            if char_obj is None:
                                if last_exc is not None:
                                    raise last_exc
                                raise RuntimeError("Control characteristic not found")

                            wrote = False
                            for use_response in (True, False):
                                try:
                                    await client.write_gatt_char(char_obj, payload, response=use_response)
                                    wrote = True
                                    break
                                except Exception as exc:
                                    last_exc = exc
                            if not wrote:
                                if last_exc is not None:
                                    raise last_exc
                                raise RuntimeError("Control characteristic write failed")
                    finally:
                        try:
                            connected_attr = getattr(client, "is_connected", False)
                            connected = connected_attr() if callable(connected_attr) else bool(connected_attr)
                            if connected:
                                await client.disconnect()
                        except Exception:
                            pass

                asyncio.run(do_write())

                with ble_lock:
                    ble_state["restart_req"] = True
                    ble_state["restart_reason"] = "gatt_write_done"
                set_status(ok_message, 2.0)
            except Exception as exc:
                gui_recal_request_pending = False
                gui_recal_started = False
                with ble_lock:
                    ble_state["restart_req"] = True
                    ble_state["restart_reason"] = "gatt_write_fail"
                set_status(f"{fail_prefix}: {type(exc).__name__}", 3.0)

        threading.Thread(target=worker, daemon=True).start()

    def request_runtime_recalibration():
        nonlocal calibration_required, calibration_done, calibration_armed
        nonlocal gui_recal_request_pending, gui_recal_started

        if BleakClient is None:
            set_status("BleakClient not available. Install bleak first.", 2.5)
            return

        with ble_lock:
            addr = ble_state.get("addr", None)
            ble_ok_local = (time.perf_counter() - float(ble_state.get("last_valid_t", 0.0) or 0.0)) <= STALE_SEC
            in_cal = bool(ble_state.get("inCal", False))

        if addr is None or (not ble_ok_local):
            set_status("Board connection is required before re-init.", 1.8)
            return

        gui_recal_request_pending = True
        gui_recal_started = bool(in_cal)

        if in_cal:
            calibration_required = True
            calibration_done = False
            calibration_armed = True
            set_status("Calibration is already in progress...", 1.5)
            return

        set_status("Sending re-init + recalibration command...", 1.8)
        send_ble_command_once(
            addr,
            bytes([BLE_CTRL_CMD_REINIT_RECAL]),
            "Re-init command sent. Waiting for calibration...",
            "Re-init command failed"
        )

    def ble_thread():
        if (BleakScanner is None) or (not USE_BLE_TELEMETRY):
            print("[BLE] bleak not available or disabled -> telemetry OFF")
            return

        async def run_scanner():
            scanning_mode = "active"

            while True:
                with ble_lock:
                    ble_state["restart_req"] = False
                    ble_state["restart_reason"] = ""

                def cb(device, adv):
                    nowp = time.perf_counter()
                    name = getattr(device, "name", None) or getattr(adv, "local_name", None)
                    addr = getattr(device, "address", None)

                    md = getattr(adv, "manufacturer_data", None)
                    if not md:
                        return

                    if target_lock["addr"] is not None:
                        if (nowp - float(target_lock["last_valid_t"] or 0.0)) > LOCK_RELEASE_SEC:
                            unlock()

                    if name is not None and ble_name_allowed(name) and addr is not None:
                        allowed_addrs.add(addr)

                    if target_lock["addr"] is not None and addr != target_lock["addr"]:
                        return

                    if target_lock["addr"] is None:
                        if name is not None:
                            if not ble_name_allowed(name):
                                return
                        else:
                            if addr not in allowed_addrs:
                                return

                    parsed = None
                    parsed_thr = None
                    if 0xFFFF in md:
                        parsed = parse_hm_payload(md.get(0xFFFF))
                    if 0xFFFE in md:
                        parsed_thr = parse_thr_payload(md.get(0xFFFE))
                    for _, payload in md.items():
                        if parsed is None:
                            p2 = parse_hm_payload(payload)
                            if p2:
                                parsed = p2
                        if parsed_thr is None:
                            t2 = parse_thr_payload(payload)
                            if t2:
                                parsed_thr = t2

                    # HT(threshold) scan-response packet may arrive without HM main payload.
                    # Cache threshold values first so recording headers can use them.
                    if parsed_thr is not None:
                        incoming_thr_mv = normalize_thr_mv_list(parsed_thr.get("thr_mv", [None] * CH_COUNT))
                        with ble_lock:
                            ble_state["thr_mv"] = incoming_thr_mv
                            ble_state["thr_mv_seen_t"] = nowp
                            # Windows/Bleak에서 초기 scan response(전 채널 1496)가 오래 남아
                            # 뒤늦게 다시 들어오는 경우가 있다. 그런 패킷은 "last seen"으로만 두고,
                            # 신뢰 가능한 threshold(thr_mv_good)는 덮어쓰지 않는다.
                            if thr_mv_is_meaningful(incoming_thr_mv):
                                ble_state["thr_mv_good"] = incoming_thr_mv
                                ble_state["thr_mv_good_t"] = nowp
                            if "battery_mv" in parsed_thr:
                                ble_state["battery_mv"] = int(parsed_thr.get("battery_mv", 0))
                                ble_state["battery_pct"] = int(parsed_thr.get("battery_pct", 0))
                                ble_state["battery_valid"] = bool(parsed_thr.get("battery_valid", False))
                                ble_state["battery_seen_t"] = nowp
                            if addr is not None:
                                ble_state["addr"] = addr
                            if name is not None:
                                ble_state["name"] = name

                    if parsed is None:
                        return

                    if target_lock["addr"] is None:
                        target_lock["addr"] = addr
                        target_lock["name"] = name
                        print(f"[BLE] LOCK -> addr={addr} name={name} mode={scanning_mode}")

                    target_lock["last_valid_t"] = nowp

                    seq = parsed.get("seq", None)
                    with ble_lock:
                        ble_state["t"] = nowp
                        ble_state["addr"] = addr
                        ble_state["name"] = name
                        ble_state["mask"] = int(parsed.get("mask", 0)) & 0x3F
                        ble_state["raw_mask"] = int(parsed.get("raw_mask", parsed.get("mask", 0))) & 0x3F
                        ble_state["cmd_mask"] = int(parsed.get("cmd_mask", parsed.get("mask", 0))) & 0x3F
                        ble_state["cmd_code"] = int(parsed.get("cmd_code", 0))
                        ble_state["cal_quality_q"] = int(parsed.get("cal_quality_q", 0))
                        ble_state["ratio"] = list(parsed.get("ratio", [0] * CH_COUNT))[:CH_COUNT]
                        ble_state["hid_connected"] = bool(parsed.get("hid_connected", False))
                        ble_state["all_valid"] = bool(parsed.get("all_valid", False))
                        ble_state["wet_lock"] = bool(parsed.get("wet_lock", False))
                        ble_state["inCal"] = bool(parsed.get("inCal", False))
                        ble_state["cal_ch"] = int(parsed.get("cal_ch", 255))
                        ble_state["stage"] = int(parsed.get("stage", 0))
                        ble_state["mv"] = parsed.get("mv", None)

                        ble_state["last_valid_t"] = nowp
                        if seq is not None:
                            if ble_state["seq"] != seq:
                                ble_state["seq"] = seq
                                ble_state["last_seq_t"] = nowp
                            else:
                                # Windows/Bleak may keep reporting the same cached advertisement
                                # for a while even though packets are still being received.
                                # Do not mark BLE as stale or force a scanner restart only because
                                # the sequence number has not advanced yet.
                                pass

                try:
                    try:
                        if scanning_mode is None:
                            scanner = BleakScanner(detection_callback=cb)
                        else:
                            scanner = BleakScanner(detection_callback=cb, scanning_mode=scanning_mode)
                    except TypeError:
                        scanner = BleakScanner(detection_callback=cb)

                    await scanner.start()
                    while True:
                        await asyncio.sleep(0.05)
                        with ble_lock:
                            if ble_state["restart_req"]:
                                ble_state["restart_req"] = False
                                ble_state["last_restart_t"] = time.perf_counter()
                                break
                finally:
                    try:
                        await scanner.stop()
                    except Exception:
                        pass
                    await asyncio.sleep(0.1)

        while True:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(run_scanner())
            except Exception:
                time.sleep(0.5)

    threading.Thread(target=ble_thread, daemon=True).start()

    # ===== 로깅 =====
    recording = False
    log_file = None
    log_writer = None
    last_log_t = 0.0
    last_flush = 0.0
    log_interval = 1.0 / LOG_HZ
    last_rec_path = ""
    log_sample_idx = 0
    log_start_path = ""
    log_device_name = "UNKNOWN"
    log_header_thr_mv = [None] * CH_COUNT

    def format_log_header_mv_labels(thr_mv):
        thr_list = list(thr_mv)[:CH_COUNT] if isinstance(thr_mv, list) else [None] * CH_COUNT
        if len(thr_list) < CH_COUNT:
            thr_list += [None] * (CH_COUNT - len(thr_list))

        labels = [
            "LK_mv()",
            "RK_mv()",
        ]

        for i, base in zip(range(2, CH_COUNT), ["UP_mv", "DOWN_mv", "LEFT_mv", "RIGHT_mv"]):
            thr = thr_list[i]
            if isinstance(thr, (int, float)):
                labels.append(f"{base}({int(thr)})")
            else:
                labels.append(f"{base}()")

        return labels

    def rewrite_csv_header_with_thresholds(csv_path: str, thr_mv):
        if not csv_path or (not os.path.exists(csv_path)):
            return

        thr_list = normalize_thr_mv_list(thr_mv)
        new_header = [
            "time", "mode", "event", "x", "y", "speed", "MT", "TP", "Path Efficiency(%)",
            "requested_command", "actual_command",
            *format_log_header_mv_labels(thr_list)
        ]

        tmp_path = csv_path + ".tmp"
        try:
            with open(csv_path, "r", newline="", encoding="utf-8") as rf, \
                 open(tmp_path, "w", newline="", encoding="utf-8") as wf:
                reader = csv.reader(rf)
                writer = csv.writer(wf)
                first = True
                for row in reader:
                    if first:
                        writer.writerow(new_header)
                        first = False
                    else:
                        writer.writerow(row)
                if first:
                    writer.writerow(new_header)
            os.replace(tmp_path, csv_path)
        except Exception as e:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            print(f"[ERR] rewrite header failed: {e}")

    def set_caption():
        mode_txt = "FULL" if fullscreen else "WIN"
        pygame.display.set_caption(f"HyoMouse GUI [{mode_txt}] (REC {'ON' if recording else 'OFF'})")

    def mode_display_name(mode):
        if mode == APP_MODE_TAPPING:
            return "Pointing Test"
        if mode == APP_MODE_TASK:
            return "Command TASK"
        if mode == APP_MODE_MAZE:
            return "Maze Navigation"
        return str(mode)

    def current_log_mode_name(mode=None):
        mode_to_use = app_mode if mode is None else mode
        ble_in_cal_now = False
        try:
            with ble_lock:
                ble_in_cal_now = bool(ble_state.get("inCal", False))
        except Exception:
            ble_in_cal_now = False

        if calibration_armed or ble_in_cal_now:
            return "Calibration"
        return mode_display_name(mode_to_use)

    def sanitize_filename_token(s: str) -> str:
        s = str(s or "").strip()
        if not s:
            return "UNKNOWN"
        out = []
        for ch in s:
            if ch.isalnum() or ch in ('-', '_'):
                out.append(ch)
            elif ch in (' ', '.', ':', '/', '\\'):
                out.append('_')
        cleaned = ''.join(out).strip('_')
        while '__' in cleaned:
            cleaned = cleaned.replace('__', '_')
        return cleaned or "UNKNOWN"

    def current_ble_device_name() -> str:
        with ble_lock:
            name = ble_state.get("name", None)
        return sanitize_filename_token(name)

    def build_log_final_path(device_name: str, dt: datetime.datetime) -> str:
        stamp = dt.strftime("%Y_%m%d_%H%M%S")
        return os.path.join(LOG_DIR, f"{sanitize_filename_token(device_name)}_{stamp}.csv")

    def start_recording():
        nonlocal recording, log_file, log_writer, last_rec_path, log_sample_idx, log_start_path, log_device_name, log_header_thr_mv
        os.makedirs(LOG_DIR, exist_ok=True)
        temp_stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(LOG_DIR, f"_recording_{temp_stamp}.csv")
        try:
            log_file = open(path, "w", newline="", encoding="utf-8")
            log_writer = csv.writer(log_file)
            log_header_thr_mv = get_best_thr_mv_snapshot()
            header = ["time", "mode", "event", "x", "y", "speed", "MT", "TP", "Path Efficiency(%)",
                      "requested_command", "actual_command"] + format_log_header_mv_labels(log_header_thr_mv)
            log_writer.writerow(header)
            log_sample_idx = 0
            recording = True
            log_start_path = path
            last_rec_path = path
            log_device_name = current_ble_device_name()
            print(f"[REC] start -> {path}")
            log_mode_change_event()
        except Exception as e:
            print(f"[ERR] start record failed: {e}")
            recording = False
            log_file = None
            log_writer = None
            log_start_path = ""
            last_rec_path = ""
            log_device_name = "UNKNOWN"
            log_header_thr_mv = [None] * CH_COUNT
        set_caption()

    def stop_recording():
        nonlocal recording, log_file, log_writer, last_rec_path, log_start_path, log_device_name, log_header_thr_mv
        end_dt = datetime.datetime.now()
        start_path_local = log_start_path
        try:
            if log_file:
                log_file.flush()
                log_file.close()
        finally:
            recording = False
            log_file = None
            log_writer = None

        final_thr_mv = get_best_thr_mv_snapshot()
        if thr_mv_is_meaningful(final_thr_mv):
            log_header_thr_mv = final_thr_mv

        final_device_name = current_ble_device_name()
        if final_device_name == "UNKNOWN":
            final_device_name = sanitize_filename_token(log_device_name)
        final_path = ""
        if start_path_local and os.path.exists(start_path_local):
            final_path = build_log_final_path(final_device_name, end_dt)
            base_no_ext, ext = os.path.splitext(final_path)
            suffix = 1
            while os.path.exists(final_path):
                final_path = f"{base_no_ext}_{suffix}{ext}"
                suffix += 1
            try:
                os.replace(start_path_local, final_path)
                last_rec_path = final_path
            except Exception as e:
                print(f"[ERR] rename record failed: {e}")
                final_path = start_path_local
                last_rec_path = start_path_local
        else:
            last_rec_path = ""

        if last_rec_path:
            rewrite_csv_header_with_thresholds(last_rec_path, log_header_thr_mv)

        log_start_path = ""
        log_device_name = final_device_name
        print(f"[REC] stop -> {last_rec_path}")
        set_caption()

    def write_log_row(pos_x, pos_y, speed, touch_mask, tp, mv, req_cmd="", act_cmd="", mode_name="", event_name="", path_eff_pct=None):
        nonlocal last_flush, log_sample_idx
        if (not recording) or (log_writer is None):
            return

        tstr = f"{int(round((1000.0 / LOG_HZ) * log_sample_idx))}ms"
        log_sample_idx += 1
        tp_str = f"{tp:.6f}" if tp is not None else ""
        pe_str = f"{path_eff_pct:.6f}" if path_eff_pct is not None else ""

        if isinstance(mv, list) and len(mv) == CH_COUNT:
            mv_cols = [int(v) for v in mv]
        else:
            mv_cols = [""] * CH_COUNT

        log_writer.writerow([
            tstr,
            mode_name,
            event_name,
            f"{pos_x:.3f}" if isinstance(pos_x, (int, float)) else "",
            f"{pos_y:.3f}" if isinstance(pos_y, (int, float)) else "",
            f"{speed:.3f}" if isinstance(speed, (int, float)) else "",
            int(touch_mask) & 0x3F if isinstance(touch_mask, (int, float)) else "",
            tp_str,
            pe_str,
            req_cmd,
            act_cmd,
            *mv_cols,
        ])

        nowp2 = time.perf_counter()
        if (nowp2 - last_flush) > 0.5 and log_file:
            log_file.flush()
            last_flush = nowp2

    def current_tap_tp():
        if (id_act_bits is not None) and (mt_avg_s is not None) and (mt_avg_s > 1e-9):
            return id_act_bits / mt_avg_s
        return None

    def current_tap_path_efficiency_pct():
        """Current Pointing Test Path Efficiency(%).

        The referenced center-out tapping metric defines PE as
        Pathopt / Pathsub * 100. In this GUI, Pathopt is the Euclidean
        distance from the position where the current target appeared to
        the target center, and Pathsub is the accumulated cursor path
        for that current target.
        """
        if app_mode != APP_MODE_TAPPING:
            return None
        if not calibration_done:
            return None
        try:
            active_ring_idx = seq_indices[seq_pos]
            tx_mm, ty_mm = targets_mm[active_ring_idx]
        except Exception:
            return None
        path_opt_mm = math.hypot(tx_mm - tap_trial_start_x_mm, ty_mm - tap_trial_start_y_mm)
        if tap_path_sub_mm <= 1e-9 or path_opt_mm <= 1e-9:
            return None
        return (path_opt_mm / tap_path_sub_mm) * 100.0

    def current_tap_path_efficiency_avg_pct():
        if tap_pe_count <= 0:
            return None
        return tap_pe_sum / float(tap_pe_count)

    def reset_tap_path_metrics(reset_average=True):
        nonlocal tap_trial_start_x_mm, tap_trial_start_y_mm, tap_path_sub_mm
        nonlocal tap_pe_last_pct, tap_pe_sum, tap_pe_count
        tap_trial_start_x_mm = x_mm
        tap_trial_start_y_mm = y_mm
        tap_path_sub_mm = 0.0
        tap_pe_last_pct = None
        if reset_average:
            tap_pe_sum = 0.0
            tap_pe_count = 0

    def start_next_tap_path_segment():
        nonlocal tap_trial_start_x_mm, tap_trial_start_y_mm, tap_path_sub_mm, tap_pe_last_pct
        tap_trial_start_x_mm = x_mm
        tap_trial_start_y_mm = y_mm
        tap_path_sub_mm = 0.0
        tap_pe_last_pct = None

    def finish_current_tap_path_segment():
        nonlocal tap_pe_last_pct, tap_pe_sum, tap_pe_count
        pe = current_tap_path_efficiency_pct()
        if pe is not None:
            tap_pe_last_pct = pe
            tap_pe_sum += pe
            tap_pe_count += 1
        return pe

    def current_path_efficiency_pct():
        # Logging/HUD should not use the instantaneous PE of an unfinished
        # pointing movement. In Center-out Tapping, PE is defined per completed
        # target and then averaged, so normal Pointing Test log rows store the
        # completed-target average. The per-target PE is written separately on
        # the target_hit event row.
        if current_log_mode_name() == "Calibration":
            return None
        if app_mode == APP_MODE_TAPPING:
            return current_tap_path_efficiency_avg_pct()
        if app_mode == APP_MODE_MAZE:
            return current_maze_path_efficiency_avg_pct()
        return None

    def current_maze_path_efficiency_avg_pct():
        if maze_pe_count <= 0:
            return None
        return maze_pe_sum / float(maze_pe_count)

    def current_maze_tp(now_perf):
        if trial_t0 is None:
            return 0.0
        cur_CT = (now_perf - trial_t0) if (maze_status == "RUNNING") else max(0.0, trial_ct)
        if cur_CT <= 0:
            return 0.0
        Pin = clamp(dist_in / (maze_total_L_label + 1e-9), 0.0, 1.0)
        Pout = clamp(dist_out / (dist_in + dist_out + 1e-9), 0.0, 1.0)
        return maze_IDp * Pin * (1.0 - Pout) / cur_CT

    def current_maze_path_metrics_px():
        """Return (path_opt_px, path_sub_px, path_eff_pct).

        path_opt is the shortest legal path inside the ㄹ-shaped maze, not the
        centerline length. path_sub is the user's actual traveled distance.
        Both are displayed in screen pixels, so maze-unit distances are
        multiplied by maze_scale.
        """
        path_opt_px = float(maze_path_opt_units) * float(maze_scale)
        path_sub_px = float(dist_in + dist_out) * float(maze_scale)
        if path_sub_px > 1e-9:
            path_eff_pct = (path_opt_px / path_sub_px) * 100.0
        else:
            path_eff_pct = 0.0
        return path_opt_px, path_sub_px, path_eff_pct

    def maybe_log(cur_speed, touch_mask, mv, pos_x, pos_y, tp):
        nonlocal last_log_t, pending_response_log
        if (not recording) or (log_writer is None):
            return

        nowp = time.perf_counter()
        if (nowp - last_log_t) < log_interval:
            return

        if pending_response_log is not None:
            row = pending_response_log
            write_log_row(
                row["x"], row["y"], row["speed"], row["touch_mask"],
                row["tp"], row["mv"],
                req_cmd=row["requested_command"],
                act_cmd=row["actual_command"],
                mode_name=current_log_mode_name(),
                path_eff_pct=current_path_efficiency_pct()
            )
            pending_response_log = None
            last_log_t = nowp
            return

        req_cmd = ""
        act_cmd = ""

        if app_mode == APP_MODE_TASK:
            req_cmd = task_current_label() if task_current_idx is not None else ""
            act_cmd = ""

        write_log_row(pos_x, pos_y, cur_speed, touch_mask, tp, mv, req_cmd=req_cmd, act_cmd=act_cmd, mode_name=current_log_mode_name(), path_eff_pct=current_path_efficiency_pct())
        last_log_t = nowp

    def log_mode_change_event(mode=None):
        if (not recording) or (log_writer is None):
            return
        write_log_row("", "", "", "", None, None, mode_name=current_log_mode_name(mode), event_name="mode_changed")

    # ===== mode helper =====
    def task_current_label():
        if task_current_idx is None:
            return ""
        return TASK_ITEMS[task_current_idx]["label"]

    def task_is_complete():
        return (len(task_sequence) > 0) and (task_current_idx is None) and (task_sequence_pos >= len(task_sequence))

    def task_current_prompt():
        if task_current_idx is None:
            if calibration_done and task_is_complete():
                return "All 10 cycles completed."
            return ""
        return TASK_ITEMS[task_current_idx]["prompt"]

    def reset_maze_trial():
        nonlocal maze_status, trial_t0, trial_ct, dist_in, dist_out
        nonlocal maze_cursor, maze_last_pos, progress_s
        maze_status = "IDLE"
        trial_t0 = None
        trial_ct = 0.0
        dist_in = 0.0
        dist_out = 0.0
        maze_cursor = [V[0][0], V[0][1]]
        maze_last_pos = (maze_cursor[0], maze_cursor[1])
        progress_s = 0.0
        maze_pos_hist.clear()

    def start_maze_trial():
        nonlocal maze_status, trial_t0, dist_in, dist_out, maze_last_pos, progress_s
        maze_status = "RUNNING"
        trial_t0 = time.perf_counter()
        dist_in = 0.0
        dist_out = 0.0
        maze_last_pos = (maze_cursor[0], maze_cursor[1])
        progress_s = 0.0

    def end_maze_trial(force_status=None):
        nonlocal maze_status, trial_ct
        nonlocal maze_pe_last_pct, maze_pe_sum, maze_pe_count
        if trial_t0 is None:
            return
        if force_status is not None:
            maze_status = force_status

        trial_ct = max(1e-6, time.perf_counter() - trial_t0)

        Pin = clamp(dist_in / (maze_total_L_label + 1e-9), 0.0, 1.0)
        total_tr = dist_in + dist_out
        Pout = clamp(dist_out / (total_tr + 1e-9), 0.0, 1.0)
        TPm = maze_IDp * Pin * (1.0 - Pout) / trial_ct
        path_opt_px, path_sub_px, path_eff_pct_raw = current_maze_path_metrics_px()

        # Maze PE is meaningful only after a completed path.
        # During RUNNING/TIMEOUT, Pathsub does not represent the complete path,
        # so values can become artificially large. Therefore, only DONE trials
        # update/log Path Efficiency(%).
        path_eff_pct = None
        if maze_status == "DONE" and path_sub_px > 1e-9:
            path_eff_pct = path_eff_pct_raw
            maze_pe_last_pct = path_eff_pct
            maze_pe_sum += path_eff_pct
            maze_pe_count += 1

        results[participant][trial] = {
            "TPm": TPm, "CT": trial_ct, "Pin": Pin, "Pout": Pout,
            "Din": dist_in, "Dout": dist_out, "status": maze_status,
            "PathOptPx": path_opt_px,
            "PathSubPx": path_sub_px,
            "PathEfficiencyPct": path_eff_pct,
        }

        if path_eff_pct is not None:
            write_log_row(
                maze_cursor[0], maze_cursor[1], "", "", TPm, None,
                mode_name="Maze Navigation",
                event_name="maze_done",
                path_eff_pct=path_eff_pct,
            )
        else:
            write_log_row(
                maze_cursor[0], maze_cursor[1], "", "", TPm, None,
                mode_name="Maze Navigation",
                event_name=f"maze_{str(maze_status).lower()}",
                path_eff_pct=None,
            )

    def maze_tpm_avg():
        vals = []
        for i in range(N_PARTICIPANTS):
            for j in range(N_TRIALS_PER_PART):
                r = results[i][j]
                if r and (r.get("TPm") is not None):
                    vals.append(float(r["TPm"]))
        if not vals:
            return None, 0
        return sum(vals)/len(vals), len(vals)

    def reset_task_plan(start_now=None, reset_scores=True):
        nonlocal task_current_idx, task_last_idx, task_feedback, task_feedback_color, task_feedback_until
        nonlocal task_next_prompt_at, task_total, task_correct
        nonlocal task_state, task_release_zero_since, task_response_deadline, task_prev_input_mask
        nonlocal task_sequence, task_sequence_pos, task_candidate_mask, task_hold_start

        task_current_idx = None
        task_last_idx = None
        task_feedback = ""
        task_feedback_color = (50, 50, 50)
        task_feedback_until = 0.0
        if reset_scores:
            task_total = 0
            task_correct = 0
        task_state = TASK_STATE_IDLE
        task_release_zero_since = None
        task_response_deadline = 0.0
        task_prev_input_mask = 0
        task_sequence = build_task_sequence(TASK_TOTAL_CYCLES)
        task_sequence_pos = 0
        task_candidate_mask = 0
        task_hold_start = 0.0
        task_next_prompt_at = time.perf_counter() if start_now is None else start_now

    def schedule_next_task(nowp: float):
        nonlocal task_current_idx, task_last_idx
        nonlocal task_state, task_release_zero_since, task_response_deadline, task_prev_input_mask
        nonlocal task_sequence_pos

        if app_mode != APP_MODE_TASK or (not calibration_done):
            task_current_idx = None
            task_state = TASK_STATE_IDLE
            return

        if task_current_idx is None and task_sequence_pos < len(task_sequence) and nowp >= task_next_prompt_at:
            task_current_idx = task_sequence[task_sequence_pos]
            task_sequence_pos += 1
            task_state = TASK_STATE_WAIT_RELEASE
            task_release_zero_since = None
            task_response_deadline = 0.0
            task_prev_input_mask = 0

    # ===== 레이아웃 =====
    def rebuild_fpcb_panel():
        nonlocal control_rect, fpcb_rect, fpcb_img_scaled, fpcb_scale, fpcb_ox, fpcb_oy

        if not SHOW_FPCB_PANEL:
            control_rect = pygame.Rect(0, 0, 0, 0)
            fpcb_rect = pygame.Rect(0, 0, 0, 0)
            fpcb_img_scaled = None
            return

        x = inner.right + FPCB_PANEL_GAP_PX
        avail_w = max(0, win_w - x - 10)
        w = min(FPCB_PANEL_W_PX, avail_w)
        total_h = inner.height
        y = inner.top

        if w <= 0 or total_h <= 0:
            control_rect = pygame.Rect(0, 0, 0, 0)
            fpcb_rect = pygame.Rect(0, 0, 0, 0)
            fpcb_img_scaled = None
            return

        btn_h = 42
        gap = 10
        panel_pad = 10
        control_h = (panel_pad * 2) + (btn_h * 4) + (gap * 3)
        control_h = min(control_h, max(120, total_h - 100))
        control_rect = pygame.Rect(x, y, w, control_h)

        fpcb_y = control_rect.bottom + 12
        fpcb_h = max(0, (y + total_h) - fpcb_y)
        fpcb_rect = pygame.Rect(x, fpcb_y, w, fpcb_h)

        if (fpcb_img_orig is None) or fpcb_rect.width <= 0 or fpcb_rect.height <= 0:
            fpcb_img_scaled = None
            return

        sw, sh = fpcb_rect.width, fpcb_rect.height
        sx = sw / fpcb_w0
        sy = sh / fpcb_h0
        fpcb_scale = min(sx, sy)
        new_w = int(round(fpcb_w0 * fpcb_scale))
        new_h = int(round(fpcb_h0 * fpcb_scale))
        tmp = pygame.transform.smoothscale(fpcb_img_orig, (new_w, new_h))
        fpcb_img_scaled = tmp

        fpcb_ox = fpcb_rect.left + (sw - new_w)//2
        fpcb_oy = fpcb_rect.top  + (sh - new_h)//2

    def rebuild_ui_buttons():
        nonlocal ui_buttons
        btn_h = 42
        gap = 10

        if SHOW_FPCB_PANEL and control_rect.width > 0:
            btn_w = max(140, control_rect.width - 20)
            bx = control_rect.left + 10
            by = control_rect.top + 10
        else:
            btn_w = min(210, max(155, win_w // 8))
            bx = max(10, inner.right - btn_w)
            by = max(10, inner.top + 10)

        ui_buttons = {
            "cal":   {"rect": pygame.Rect(bx, by + 0*(btn_h+gap), btn_w, btn_h), "label": "Calibration"},
            "recal": {"rect": pygame.Rect(bx, by + 1*(btn_h+gap), btn_w, btn_h), "label": "Re-Cal"},
            "tap":   {"rect": pygame.Rect(bx, by + 2*(btn_h+gap), btn_w, btn_h), "label": "Pointing Test"},
            "task":  {"rect": pygame.Rect(bx, by + 3*(btn_h+gap), btn_w, btn_h), "label": "Command TASK"},
            "maze":  {"rect": pygame.Rect(bx, by + 4*(btn_h+gap), btn_w, btn_h), "label": "Maze Navigation"},
        }

    def rebuild_tapping_layout():
        nonlocal tap_px_per_mm, tap_mm_per_px, tap_border_px, tap_inner_side, inner
        nonlocal target_r_px, target_r_mm, ring_r_mm, cursor_r_px, targets_mm, id_act_bits

        usable_w = win_w - (FPCB_PANEL_W_PX + FPCB_PANEL_GAP_PX + 20 if SHOW_FPCB_PANEL else 0)
        est_px_per_mm = min(usable_w / AREA_W_MM, win_h / AREA_H_MM)

        tap_border_px = max(2, int(round(BORDER_MM * est_px_per_mm)))
        usable_w2 = win_w - 2*tap_border_px - (FPCB_PANEL_W_PX + FPCB_PANEL_GAP_PX + 20 if SHOW_FPCB_PANEL else 0)
        tap_inner_side = min(usable_w2, win_h - 2*tap_border_px)

        tap_px_per_mm  = tap_inner_side / AREA_W_MM
        tap_mm_per_px  = 1.0 / tap_px_per_mm
        tap_border_px  = max(2, int(round(BORDER_MM * tap_px_per_mm)))
        cursor_r_px = max(2, int(round(CURSOR_RADIUS_MM * tap_px_per_mm)))

        inner_left = tap_border_px
        inner_top  = (win_h - tap_inner_side) // 2
        inner      = pygame.Rect(inner_left, inner_top, tap_inner_side, tap_inner_side)

        D  = float(TARGET_DIAMETER_PX)
        target_r_px = max(2, int(round(D / 2.0)))
        target_r_mm = (D / 2.0) * tap_mm_per_px

        N = float(TARGET_COUNT)
        k = TARGET_COUNT // 2
        sin_term = math.sin(math.pi * (k / N))
        sin_term = max(1e-9, sin_term)

        Amax_req_px = float(EDGE_SPACING_PX)
        Re = Amax_req_px / (2.0 * sin_term)

        max_ring_r_mm = AREA_W_MM/2.0 - target_r_mm - MARGIN_MM
        ring_r_px_allow = max_ring_r_mm * tap_px_per_mm

        R_used_px = min(Re, ring_r_px_allow)
        ring_r_mm = R_used_px * tap_mm_per_px

        Amax_act_px = 2.0 * R_used_px * sin_term
        if D > 1e-9:
            id_act_bits = math.log2(Amax_act_px / D + 1.0)
        else:
            id_act_bits = None

        center_mm = (AREA_W_MM/2.0, AREA_H_MM/2.0)
        step_deg = 360.0 / float(TARGET_COUNT)
        new_targets = []
        for i in range(TARGET_COUNT):
            ang = math.radians(step_deg * i)
            x = center_mm[0] + ring_r_mm * math.cos(ang)
            y = center_mm[1] + ring_r_mm * math.sin(ang)
            new_targets.append((x, y))
        targets_mm = new_targets

        rebuild_fpcb_panel()
        rebuild_ui_buttons()

    def rebuild_maze_layout_and_cache():
        nonlocal inner, maze_scale, maze_offx, maze_offy
        nonlocal maze_polys_px, maze_arrows_px, maze_label_px, maze_len_px, maze_start_px, maze_end_px

        usable_w = win_w - (FPCB_PANEL_W_PX + FPCB_PANEL_GAP_PX + 20 if SHOW_FPCB_PANEL else 0)
        inner_left = 14
        inner_top  = 14
        inner_w    = usable_w - inner_left - 14
        inner_h    = win_h - 2*inner_top
        inner = pygame.Rect(inner_left, inner_top, inner_w, inner_h)

        minx, miny, maxx, maxy = maze_bounds
        bw = maxx - minx
        bh = maxy - miny
        sx = (inner.width  - 60) / bw
        sy = (inner.height - 60) / bh
        maze_scale = max(0.1, min(sx, sy))
        maze_offx = inner.left + (inner.width  - bw * maze_scale) * 0.5 - minx * maze_scale
        maze_offy = inner.top  + (inner.height - bh * maze_scale) * 0.5 - miny * maze_scale

        rebuild_fpcb_panel()
        rebuild_ui_buttons()

        def u2px(p):
            return (int(round(maze_offx + p[0]*maze_scale)),
                    int(round(maze_offy + p[1]*maze_scale)))

        P,T,W,S,meta_mid = sample_centerline(V, maze_Wv, SAMPLE_STEP_UNITS)
        polys_units = build_track_polygons(P, T, W, EDGE_PAD_UNITS)

        maze_polys_px = {}
        for k,poly_u in polys_units.items():
            maze_polys_px[k] = [(int(round(maze_offx + x*maze_scale)), int(round(maze_offy + y*maze_scale))) for (x,y) in poly_u]

        maze_arrows_px = []
        arrow_gap = max(40.0, 80.0 * maze_scale)
        acc = 0.0
        for i in range(1, len(P)):
            p0 = u2px(P[i-1]); p1 = u2px(P[i])
            segd = math.hypot(p1[0]-p0[0], p1[1]-p0[1])
            acc += segd
            if acc >= arrow_gap:
                acc = 0.0
                tx,ty = T[i]
                cx,cy = u2px(P[i])
                size = max(10, int(14*maze_scale))
                nx,ny = -ty, tx
                tip = (cx + int(tx*size), cy + int(ty*size))
                b1  = (cx - int(tx*size*0.6) + int(nx*size*0.5), cy - int(ty*size*0.6) + int(ny*size*0.5))
                b2  = (cx - int(tx*size*0.6) - int(nx*size*0.5), cy - int(ty*size*0.6) - int(ny*size*0.5))
                maze_arrows_px.append([tip,b1,b2])

        maze_label_px = []
        maze_len_px   = []
        for (idx, mid, tan) in meta_mid:
            s = maze_segs[idx]
            letter = s["name"]
            if letter <= "G": cc = COL_A_G
            elif letter <= "N": cc = COL_H_N
            else: cc = COL_O_U

            tx,ty = tan
            nx,ny = -ty, tx
            off = (maze_Wv[idx] * 0.45)
            lx = mid[0] + nx*off
            ly = mid[1] + ny*off
            maze_label_px.append((letter, u2px((lx,ly)), cc))

            off2 = (maze_Wv[idx] * 0.18)
            lx2 = mid[0] - nx*off2
            ly2 = mid[1] - ny*off2
            maze_len_px.append((str(int(round(s["L"]))), u2px((lx2,ly2))))

        maze_start_px = u2px(V[0])
        maze_end_px   = u2px(V[-1])

    def rebuild_layout():
        if app_mode in (APP_MODE_TAPPING, APP_MODE_TASK):
            rebuild_tapping_layout()
        else:
            rebuild_maze_layout_and_cache()

    def set_mode(new_mode):
        nonlocal app_mode, task_state
        old_mode = app_mode
        app_mode = new_mode
        if new_mode == APP_MODE_TASK:
            reset_task_plan(time.perf_counter(), reset_scores=True)
            set_status("Command TASK", 1.0)
        elif new_mode == APP_MODE_TAPPING:
            task_state = TASK_STATE_IDLE
            reset_tap_path_metrics(reset_average=True)
            set_status("Pointing Test", 1.0)
        elif new_mode == APP_MODE_MAZE:
            task_state = TASK_STATE_IDLE
            set_status("Maze Navigation", 1.0)
        if new_mode != old_mode:
            log_mode_change_event(new_mode)
        rebuild_layout()

    rebuild_layout()
    set_caption()
    reset_maze_trial()

    pygame.event.set_grab(False)
    pygame.mouse.set_visible(False)
    mx0, my0 = pygame.mouse.get_pos()
    gui_cursor_px = int(clamp(mx0, 0, win_w - 1))
    gui_cursor_py = int(clamp(my0, 0, win_h - 1))

    # ===== draw helper =====
    def to_px_tap(xm, ym):
        return (
            int(round(inner.left + xm * tap_px_per_mm)),
            int(round(inner.top  + ym * tap_px_per_mm))
        )

    def maze_px_to_units(dx_px, dy_px):
        return (dx_px / maze_scale, dy_px / maze_scale)

    def warp_gui_cursor(px, py):
        nonlocal gui_cursor_px, gui_cursor_py
        px = int(clamp(round(px), 0, win_w - 1))
        py = int(clamp(round(py), 0, win_h - 1))
        gui_cursor_px = px
        gui_cursor_py = py
        pygame.mouse.set_pos((px, py))

    def move_gui_cursor_to_tap_center():
        nonlocal x_mm, y_mm
        x_mm = AREA_W_MM / 2.0
        y_mm = AREA_H_MM / 2.0
        reset_tap_path_metrics(reset_average=True)
        px, py = to_px_tap(x_mm, y_mm)
        warp_gui_cursor(px, py)

    def move_gui_cursor_to_maze_start():
        nonlocal maze_cursor, maze_last_pos
        maze_cursor[0] = V[0][0]
        maze_cursor[1] = V[0][1]
        maze_last_pos = (maze_cursor[0], maze_cursor[1])
        px = int(round(maze_offx + maze_cursor[0] * maze_scale))
        py = int(round(maze_offy + maze_cursor[1] * maze_scale))
        warp_gui_cursor(px, py)

    def get_pad_display_def(pad):
        if pad not in PAD_DEFS:
            return None
        d = PAD_DEFS[pad]
        if not FPCB_MIRROR_X:
            return d
        if d["type"] == "circle":
            return {"type": "circle", "cx": fpcb_w0 - d["cx"], "cy": d["cy"], "r": d["r"]}
        return {
            "type": "rect",
            "x1": fpcb_w0 - d["x2"],
            "y1": d["y1"],
            "x2": fpcb_w0 - d["x1"],
            "y2": d["y2"],
        }

    def draw_pad_overlay(pad, alpha, color):
        if fpcb_img_scaled is None:
            return
        if pad not in PAD_DEFS:
            return
        d = get_pad_display_def(pad)
        if d is None:
            return
        overlay = pygame.Surface((fpcb_rect.width, fpcb_rect.height), pygame.SRCALPHA)
        c = (*color, alpha)

        def ix(x):
            return int(round((x * fpcb_scale) + (fpcb_ox - fpcb_rect.left)))
        def iy(y):
            return int(round((y * fpcb_scale) + (fpcb_oy - fpcb_rect.top)))

        if d["type"] == "circle":
            cx = ix(d["cx"]); cy = iy(d["cy"])
            rr = int(round(d["r"] * fpcb_scale))
            pygame.draw.circle(overlay, c, (cx, cy), rr)
            pygame.draw.circle(overlay, (*color, min(255, alpha+60)), (cx, cy), rr, 2)
        else:
            x1 = ix(d["x1"]); y1 = iy(d["y1"])
            x2 = ix(d["x2"]); y2 = iy(d["y2"])
            r = pygame.Rect(min(x1,x2), min(y1,y2), abs(x2-x1), abs(y2-y1))
            pygame.draw.rect(overlay, c, r, border_radius=10)
            pygame.draw.rect(overlay, (*color, min(255, alpha+60)), r, 2, border_radius=10)

        screen.blit(overlay, (fpcb_rect.left, fpcb_rect.top))

    def pad_anchor_xy(pad):
        if pad not in PAD_DEFS:
            return None
        d = get_pad_display_def(pad)
        if d is None:
            return None
        def ix(x):
            return int(round((x * fpcb_scale) + (fpcb_ox - fpcb_rect.left)))
        def iy(y):
            return int(round((y * fpcb_scale) + (fpcb_oy - fpcb_rect.top)))
        if d["type"] == "circle":
            return ix(d["cx"]), iy(d["cy"])
        return ix((d["x1"] + d["x2"]) * 0.5), iy((d["y1"] + d["y2"]) * 0.5)

    def draw_pad_text_badges():
        if fpcb_img_scaled is None:
            return
        for pad, label in PAD_LABEL_TEXT.items():
            pt = pad_anchor_xy(pad)
            d = get_pad_display_def(pad)
            if pt is None or d is None:
                continue
            cx, cy = pt
            if d["type"] == "circle":
                font_sz = max(14, int(16 * fpcb_scale))
                off_y = -2
            else:
                font_sz = max(14, int(15 * fpcb_scale))
                off_y = 0
            badge_font = get_font(font_sz, bold=True)
            surf = badge_font.render(label, True, (20, 20, 20))
            bg = surf.get_rect(center=(fpcb_rect.left + cx, fpcb_rect.top + cy + off_y))
            bg.inflate_ip(12, 8)
            panel = pygame.Surface((bg.width, bg.height), pygame.SRCALPHA)
            pygame.draw.rect(panel, (255, 255, 255, 232), panel.get_rect(), border_radius=10)
            pygame.draw.rect(panel, (90, 100, 120), panel.get_rect(), 1, border_radius=10)
            panel.blit(surf, surf.get_rect(center=(bg.width//2, bg.height//2)))
            screen.blit(panel, bg.topleft)

    def draw_notice_box(text, x, y, w, h):
        panel = pygame.Surface((w, h), pygame.SRCALPHA)
        panel.fill((255, 255, 255, 220))
        pygame.draw.rect(panel, (220, 60, 60), panel.get_rect(), 2)
        yy = 10
        for ln in text.split("\n"):
            surf = font_small.render(ln, True, (40,40,40))
            panel.blit(surf, (10, yy))
            yy += surf.get_height() + 4
        screen.blit(panel, (x, y))

    def draw_button(rect, label, active=False):
        if rect.width <= 0 or rect.height <= 0:
            return
        base = (248, 248, 249)
        edge = (160, 164, 170)
        fill = base
        text_col = (36, 36, 40)
        if active:
            fill = (70, 126, 235)
            edge = (52, 96, 190)
            text_col = (255, 255, 255)

        shadow = pygame.Rect(rect.x, rect.y + 2, rect.w, rect.h)
        pygame.draw.rect(screen, (0, 0, 0, 26), shadow, border_radius=12)
        pygame.draw.rect(screen, fill, rect, border_radius=12)
        pygame.draw.rect(screen, edge, rect, 2, border_radius=12)

        font = get_font(max(18, min(26, rect.height // 2 + 2)), bold=True)
        surf = font.render(label, True, text_col)
        if surf.get_width() > rect.width - 16:
            scale = max(14, int((rect.width - 16) / max(1, len(label)) * 1.45))
            font = get_font(scale, bold=True)
            surf = font.render(label, True, text_col)
        screen.blit(surf, surf.get_rect(center=rect.center))

    def draw_fpcb_panel(raw_touch_mask, cmd_touch_mask, ble_inCal, ble_cal_ch, ble_stage, mv, ratio, cal_quality_q, cmd_code, wet_lock=False):
        if not SHOW_FPCB_PANEL:
            return

        if fpcb_rect.width > 0 and fpcb_rect.height > 0:
            pygame.draw.rect(screen, (245, 246, 248), fpcb_rect)
            pygame.draw.rect(screen, (60,60,60), fpcb_rect, 1)

        if fpcb_img_scaled is not None:
            screen.blit(fpcb_img_scaled, (fpcb_ox, fpcb_oy))
            draw_pad_text_badges()
        elif fpcb_rect.width > 0 and fpcb_rect.height > 0:
            msg = font_small.render("FPCB image not loaded", True, (200,200,200))
            screen.blit(msg, (fpcb_rect.left+10, fpcb_rect.top+10))

        if ble_inCal and (0 <= int(ble_cal_ch) < CH_COUNT):
            pad = CH_TO_PAD[int(ble_cal_ch)]
            if ble_stage in (1, 2, 5, 6, 7):
                draw_pad_overlay(pad, 140, COLOR_CAL_PRE_TOUCH)
            elif ble_stage in (3, 4):
                draw_pad_overlay(pad, 140, COLOR_TOUCH_RED)

        for ch in range(CH_COUNT):
            if raw_touch_mask & (1 << ch):
                draw_pad_overlay(CH_TO_PAD[ch], 110, (255, 160, 70))
        for ch in range(CH_COUNT):
            if cmd_touch_mask & (1 << ch):
                draw_pad_overlay(CH_TO_PAD[ch], 190, COLOR_TOUCH_RED)

        if fpcb_rect.width > 40 and fpcb_rect.height > 220:
            ratio = ratio if (isinstance(ratio, list) and len(ratio) == CH_COUNT) else [0] * CH_COUNT
            bx = fpcb_rect.left + 10
            bw = fpcb_rect.width - 20
            bh = 112
            by = fpcb_rect.bottom - bh - 10
            panel = pygame.Surface((bw, bh), pygame.SRCALPHA)
            panel.fill((255,255,255,228))
            pygame.draw.rect(panel, (180,180,180), panel.get_rect(), 1, border_radius=10)
            title = font_small.render(f"CAL Q={cal_quality_q:3d}   CMD={CMD_CODE_LABELS.get(cmd_code, '') or 'NONE'}", True, (50,50,50))
            panel.blit(title, (10, 6))

            label_font = get_font(13, bold=True)
            short_labels = ["", "", "UP", "DN", "LT", "RT"]
            left_pad = 10
            top_y = 28
            bottom_y = bh - 18
            max_h = bottom_y - top_y
            gap = 10
            bar_w = max(18, (bw - left_pad * 2 - gap * (CH_COUNT - 1)) // CH_COUNT)
            for ch in range(CH_COUNT):
                rx = left_pad + ch * (bar_w + gap)
                rr = int(clamp(ratio[ch], 0, 255))
                fill_h = int(round(max_h * (rr / 255.0)))
                pygame.draw.rect(panel, (225,228,235), (rx, top_y, bar_w, max_h), border_radius=6)
                if fill_h > 0:
                    pygame.draw.rect(panel, (255, 120, 80), (rx, top_y + max_h - fill_h, bar_w, fill_h), border_radius=6)
                pygame.draw.rect(panel, (150,160,175), (rx, top_y, bar_w, max_h), 1, border_radius=6)
                lab = label_font.render(short_labels[ch], True, (60,60,60))
                panel.blit(lab, lab.get_rect(center=(rx + bar_w // 2, bh - 9)))
            screen.blit(panel, (bx, by))

        if raw_touch_mask == 0 and cmd_touch_mask == 0 and (not ble_inCal) and cal_quality_q == 0 and cmd_code == 0:
            note = font_small.render("BLE data not received: check board advertising.", True, (180, 40, 40))
            screen.blit(note, (fpcb_rect.left + 12, fpcb_rect.top + 12))

        if wet_lock and fpcb_rect.width > 30 and fpcb_rect.height > 80:
            bx = fpcb_rect.left + 12
            by = fpcb_rect.top + 12
            bw = fpcb_rect.width - 24
            bh = 64
            draw_notice_box("Multiple wet contacts detected.\nRelease all electrodes to recover automatically.", bx, by, bw, bh)

        if ble_inCal and ble_stage in (5, 6, 7) and fpcb_rect.width > 30 and fpcb_rect.height > 90:
            bx = fpcb_rect.left + 12
            by = fpcb_rect.bottom - 80
            bw = fpcb_rect.width - 24
            bh = 68
            msg = "Touch was released during capture.\nTouch again to restart automatically."
            if ble_stage == 6:
                msg = "Calibration strength is too weak.\nTouch more firmly."
            elif ble_stage == 7:
                msg = "Calibration failed.\nAdjust the fit and try again."
            draw_notice_box(msg, bx, by, bw, bh)

    def draw_top_controls():
        cal_active = calibration_armed and (not calibration_done)
        recal_active = gui_recal_request_pending
        tap_active = (app_mode == APP_MODE_TAPPING)
        task_active = (app_mode == APP_MODE_TASK)
        maze_active = (app_mode == APP_MODE_MAZE)

        if SHOW_FPCB_PANEL and control_rect.width > 0 and control_rect.height > 0:
            pygame.draw.rect(screen, (240, 242, 246), control_rect, border_radius=14)
            pygame.draw.rect(screen, (150, 154, 160), control_rect, 1, border_radius=14)

        draw_button(ui_buttons["cal"]["rect"],   ui_buttons["cal"]["label"],   active=cal_active)
        draw_button(ui_buttons["recal"]["rect"], ui_buttons["recal"]["label"], active=recal_active)
        draw_button(ui_buttons["tap"]["rect"],   ui_buttons["tap"]["label"],   active=tap_active)
        draw_button(ui_buttons["task"]["rect"],  ui_buttons["task"]["label"],  active=task_active)
        draw_button(ui_buttons["maze"]["rect"],  ui_buttons["maze"]["label"],  active=maze_active)

    def draw_status_bar():
        if time.perf_counter() > status_until:
            return
        font = get_font(22, bold=True)
        surf = font.render(status_text, True, (30, 30, 30))
        pad_x, pad_y = 16, 10
        box = pygame.Rect(
            inner.left + 8,
            inner.bottom - surf.get_height() - pad_y * 2 - 8,
            surf.get_width() + pad_x * 2,
            surf.get_height() + pad_y * 2,
        )
        panel = pygame.Surface((box.width, box.height), pygame.SRCALPHA)
        panel.fill((255, 255, 255, 220))
        pygame.draw.rect(panel, (180, 180, 180), panel.get_rect(), 1, border_radius=10)
        screen.blit(panel, box.topleft)
        screen.blit(surf, (box.left + pad_x, box.top + pad_y))

    def draw_rec_badge():
        if not recording:
            return
        font = get_font(28, bold=True)
        text = font.render("REC", True, (255, 70, 70))
        pad = 10
        box = pygame.Surface((text.get_width()+pad*2, text.get_height()+pad), pygame.SRCALPHA)
        box.fill((0,0,0,140))
        screen.blit(box, (inner.right - box.get_width() - 12, inner.top + 12))
        screen.blit(text, (inner.right - text.get_width() - 12 - pad, inner.top + 12 + (pad//2)))

    def draw_gui_cursor():
        cx, cy = int(gui_cursor_px), int(gui_cursor_py)
        col = (40, 40, 40)

        if app_mode == APP_MODE_MAZE and inner.collidepoint(cx, cy):
            inside_now = point_inside_variable_width(maze_cursor[0], maze_cursor[1], V, maze_Wv)
            col = COL_CURSOR if inside_now else COL_CURSOR_BAD
        elif app_mode in (APP_MODE_TAPPING, APP_MODE_TASK) and inner.collidepoint(cx, cy):
            col = (50, 50, 50)
        else:
            col = (70, 70, 70)

        pygame.draw.circle(screen, col, (cx, cy), 8, 2)
        pygame.draw.line(screen, col, (cx - 10, cy), (cx + 10, cy), 1)
        pygame.draw.line(screen, col, (cx, cy - 10), (cx, cy + 10), 1)

    def draw_calibration_blocker():
        if calibration_done:
            return
        if not calibration_required:
            return

        overlay = pygame.Surface((inner.width, inner.height), pygame.SRCALPHA)
        overlay.fill((255, 255, 255, 130))
        screen.blit(overlay, inner.topleft)

        font_t = get_font(34, bold=True)
        font_b = get_font(24, bold=False)

        lines = [
            "시작 전 캘리브레이션이 필요합니다.",
            "우측 상단의 '캘리브레이션' 버튼을 먼저 클릭하세요.",
        ]

        box_w = min(inner.width - 80, 880)
        box_h = 180
        bx = inner.left + (inner.width - box_w) // 2
        by = inner.top + (inner.height - box_h) // 2
        panel = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
        panel.fill((255, 255, 255, 235))
        pygame.draw.rect(panel, (90, 120, 180), panel.get_rect(), 2, border_radius=16)

        y = 20
        title = font_t.render(lines[0], True, (35, 35, 35))
        panel.blit(title, (20, y))
        y += 60

        for ln in lines[1:]:
            surf = font_b.render(ln, True, (55, 55, 55))
            panel.blit(surf, (20, y))
            y += 36

        screen.blit(panel, (bx, by))

    def draw_task_panel():
        title_font = get_font(38, bold=True)
        body_font  = get_font(28, bold=False)
        small_font = get_font(24, bold=False)

        panel_w = min(inner.width - 40, 760)
        panel_h = 210
        px = inner.left + (inner.width - panel_w) // 2
        py = inner.top + (inner.height - panel_h) // 2
        panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
        panel.fill((255, 255, 255, 225))
        pygame.draw.rect(panel, (170, 170, 170), panel.get_rect(), 2, border_radius=16)

        title = title_font.render("Command TASK", True, (35, 35, 35))
        panel.blit(title, (24, 20))

        if not calibration_done:
            lines = [
                "Click the Calibration button first.",
                "Command TASK becomes available after calibration."
            ]
            yy = 82
            for ln in lines:
                surf = body_font.render(ln, True, (50, 50, 50))
                panel.blit(surf, (24, yy))
                yy += 42
        else:
            prompt = task_current_prompt() if task_current_prompt() else "Preparing the next command..."
            surf = body_font.render(prompt, True, (40, 40, 40))
            panel.blit(surf, (24, 84))

            if time.perf_counter() <= task_feedback_until and task_feedback:
                fb = small_font.render(task_feedback, True, task_feedback_color)
                panel.blit(fb, (24, 140))

            progress_txt = f"Progress {min(task_sequence_pos, len(task_sequence))}/{len(task_sequence) if task_sequence else TASK_TOTAL_CYCLES * len(TASK_ITEMS)}"
            score_txt = f"Correct {task_correct} / Attempts {task_total}   {progress_txt}"
            score = small_font.render(score_txt, True, (70, 70, 70))
            panel.blit(score, (24, panel_h - 40))

        screen.blit(panel, (px, py))

    def draw_tapping_scene():
        font_lbl = get_font(22, bold=True)
        active_ring_idx = seq_indices[seq_pos]

        for i, (tx, ty) in enumerate(targets_mm):
            px, py = to_px_tap(tx, ty)
            label = labels_ring[i]

            if calibration_done and i == active_ring_idx:
                pygame.draw.circle(screen, (126,200,90), (px, py), target_r_px)
                pygame.draw.circle(screen, (90,120,70), (px, py), target_r_px, 2)
                surf = font_lbl.render(label, True, (255,255,255))
            else:
                pygame.draw.circle(screen, (245,246,247), (px, py), target_r_px)
                pygame.draw.circle(screen, (150,150,150), (px, py), target_r_px, 2)
                surf = font_lbl.render(label, True, (150,150,150))

            screen.blit(surf, surf.get_rect(center=(px, py)))
            pygame.draw.circle(screen, (120,120,120), (px, py), 2, 0)


    def draw_maze_scene(raw_touch_mask, cmd_touch_mask, ble_ok, ble_seq, ble_inCal, ble_cal_ch, ble_stage, mv, ratio, dev_name, ble_cmd_code, ble_cal_quality_q, ble_hid_connected, ble_all_valid, ble_wet_lock, battery_mv, battery_pct, battery_valid):
        screen.fill(COL_BG)
        pygame.draw.rect(screen, COL_PANEL_BG, inner, border_radius=14)
        pygame.draw.rect(screen, (45,50,60), inner, 2, border_radius=14)

        if maze_polys_px:
            pygame.gfxdraw.filled_polygon(screen, maze_polys_px["edge"], COL_TRACK_EDGE)
            pygame.gfxdraw.aapolygon(screen, maze_polys_px["edge"], COL_TRACK_EDGE)

            pygame.gfxdraw.filled_polygon(screen, maze_polys_px["fill"], COL_TRACK_FILL)
            pygame.gfxdraw.aapolygon(screen, maze_polys_px["fill"], COL_TRACK_FILL)

            pygame.gfxdraw.filled_polygon(screen, maze_polys_px["inner"], COL_TRACK_INNER)
            pygame.gfxdraw.aapolygon(screen, maze_polys_px["inner"], COL_TRACK_INNER)

        for tri in maze_arrows_px:
            pygame.gfxdraw.filled_polygon(screen, tri, COL_ARROW)
            pygame.gfxdraw.aapolygon(screen, tri, COL_ARROW)

        for (letter, (px,py), cc) in maze_label_px:
            surf = font_med.render(letter, True, cc)
            screen.blit(surf, surf.get_rect(center=(px,py)))

        for (ln, (px,py)) in maze_len_px:
            surf = font_len.render(ln, True, (20,20,20))
            bg = pygame.Surface((surf.get_width()+8, surf.get_height()+4), pygame.SRCALPHA)
            bg.fill((255,255,255,170))
            r = bg.get_rect(center=(px,py))
            screen.blit(bg, r.topleft)
            screen.blit(surf, surf.get_rect(center=(px,py)))

        s1 = font_med.render("Start", True, COL_TEXT)
        s2 = font_med.render("End", True, COL_TEXT)
        screen.blit(s1, (maze_start_px[0]-s1.get_width()//2, maze_start_px[1]-36))
        screen.blit(s2, (maze_end_px[0]-s2.get_width()//2, maze_end_px[1]+10))

        lx = inner.left + 18
        ly = inner.bottom - 98
        title = font_med.render("Width", True, COL_TEXT)
        screen.blit(title, (lx, ly))
        yy = ly + 30
        for tag, wv0, cc in [("A-G", W_AG, COL_A_G), ("H-N", W_HN, COL_H_N), ("O-U", W_OU, COL_O_U)]:
            t = font_small.render(f"{tag:>3}  {int(wv0)}", True, cc)
            screen.blit(t, (lx, yy))
            yy += 20

        hud_rect = None
        if SHOW_HUD:
            cur_CT = (time.perf_counter()-trial_t0) if (trial_t0 and maze_status=="RUNNING") else (trial_ct if trial_t0 else 0.0)
            Pin = clamp(dist_in / (maze_total_L_label + 1e-9), 0.0, 1.0)
            Pout = clamp(dist_out / (dist_in+dist_out+1e-9), 0.0, 1.0)
            TPm = (maze_IDp * Pin * (1.0 - Pout) / max(1e-6, cur_CT)) if (trial_t0 and cur_CT>0) else 0.0
            remain = 0.0
            if trial_t0 and maze_status=="RUNNING":
                remain = max(0.0, TRIAL_TIME_LIMIT_SEC - (time.perf_counter()-trial_t0))
            avg, cnt = maze_tpm_avg()

            if isinstance(mv, list) and len(mv) == CH_COUNT:
                mv_txt = " ".join(f"{MV_LABELS[i]}={int(mv[i])}" for i in range(CH_COUNT))
            else:
                mv_txt = "—"

            cal_txt = "CAL: OFF"
            if ble_inCal:
                st = CAL_STAGE_NAMES.get(ble_stage, str(ble_stage))
                mv_one = "—"
                if isinstance(mv, list) and len(mv) == CH_COUNT and (0 <= ble_cal_ch <= (CH_COUNT-1)):
                    mv_one = f"{int(mv[ble_cal_ch])}mV"
                cal_txt = f"CAL: {st}  ch={ble_cal_ch}  mv={mv_one}"

            prog_pct = (progress_s / max(1e-6, maze_total_geom_len)) * 100.0
            maze_pe_avg = current_maze_path_efficiency_avg_pct()
            maze_pe_txt = f"{maze_pe_last_pct:.2f}" if maze_pe_last_pct is not None else "—"
            maze_pe_avg_txt = f"{maze_pe_avg:.2f}" if maze_pe_avg is not None else "—"

            lines = [
                f"[Maze Navigation] status={maze_status}  participant={participant+1}/{N_PARTICIPANTS}  trial={trial+1}/{N_TRIALS_PER_PART}",
                f"Eq(2) IDp={maze_IDp:.3f} bits  (segments={len(maze_segs)})",
                f"Pin={Pin:.3f}  Pout={Pout:.3f}  CT={cur_CT:.2f}s  TP={TPm:.3f} bps  remain={remain:.1f}s",
                (f"TPmavg={avg:.3f} bps (count={cnt})" if avg is not None else "TPmavg=—"),
                f"PROGRESS: {prog_pct:5.1f}%",
                f"Path Efficiency(%) {maze_pe_txt}  avg={maze_pe_avg_txt}  count={maze_pe_count}",
                f"BLE {'OK' if ble_ok else 'STALE'}  seq={ble_seq if ble_seq is not None else '—'}  raw=0b{raw_touch_mask:06b}  cmd=0b{cmd_touch_mask:06b}",
                f"DEV={dev_name or '—'}  HID={'ON' if ble_hid_connected else 'OFF'}  VALID={'ALL' if ble_all_valid else 'PARTIAL'}  WET={'ON' if ble_wet_lock else 'OFF'}  CMD={CMD_CODE_LABELS.get(ble_cmd_code, '') or 'NONE'}",
                f"BAT={battery_text(battery_mv, battery_pct, battery_valid)}",
                cal_txt + f"  Q={ble_cal_quality_q}",
                f"mv(mV): {mv_txt}",
                f"ratio(Q8): {' '.join(str(int(v)) for v in ratio) if isinstance(ratio, list) else '—'}",
                f"GAIN={MOUSE_GAIN:.2f}",
                f"CSV_LOG={'ON' if recording else 'OFF'}",
            ]

            pads,gap = 10,4
            surfs = [font_hud.render(s, True, COL_TEXT) for s in lines]
            w = max(s.get_width() for s in surfs) + 2*pads
            h = sum(s.get_height() for s in surfs) + (len(surfs)-1)*gap + 2*pads
            panel = pygame.Surface((w,h), pygame.SRCALPHA)
            panel.fill((0,0,0,150))
            pygame.draw.rect(panel, (150,150,150,170), panel.get_rect(), 1)
            yy = pads
            for s in surfs:
                panel.blit(s, (pads, yy))
                yy += s.get_height() + gap

            hud_x = inner.left + 14
            hud_y = inner.top + 14
            screen.blit(panel, (hud_x, hud_y))
            hud_rect = pygame.Rect(hud_x, hud_y, w, h)

        if SHOW_HELP:
            lines = [
                "Maze Keys:",
                "SPACE : start trial",
                "ENTER : force DONE",
                "BACKSPACE : force TIMEOUT",
                "N/B : next/prev trial   ]/[ : next/prev participant",
                "R : reset trial",
                "+/- : gain",
                "Arrow keys : move cursor",
                "1..6 : simulate electrode touch",
                "Q : fullscreen toggle",
                "F1 : toggle help   H : toggle HUD",
            ]
            pads,gap = 10,3
            surfs = [font_small.render(s, True, COL_TEXT_DIM) for s in lines]
            w = max(s.get_width() for s in surfs) + 2*pads
            h = sum(s.get_height() for s in surfs) + (len(surfs)-1)*gap + 2*pads
            panel = pygame.Surface((w,h), pygame.SRCALPHA)
            panel.fill((0,0,0,140))
            pygame.draw.rect(panel, (120,120,120,150), panel.get_rect(), 1)
            yy = pads
            for s in surfs:
                panel.blit(s, (pads, yy))
                yy += s.get_height() + gap

            hx = inner.left + 14
            hy = inner.top + 14
            if hud_rect is not None:
                hy = hud_rect.bottom + 10
            screen.blit(panel, (hx, hy))

    def draw_common_hud(spd, hd, ble_ok, ble_seq, ble_raw_mask, ble_cmd_mask, ble_cmd_code, ble_inCal, ble_cal_ch, ble_stage, mv, ble_ratio, ble_cal_quality_q, ble_hid_connected, ble_all_valid, ble_wet_lock, dev_name, dev_addr, battery_mv, battery_pct, battery_valid):
        if app_mode == APP_MODE_MAZE:
            return
        if not SHOW_HUD:
            return

        font_s = get_font(20, bold=False)
        tp = current_tap_tp()

        cal_txt = f"CAL_REQUIRED={'YES' if calibration_required else 'NO'}  DONE={'YES' if calibration_done else 'NO'}  ARMED={'YES' if calibration_armed else 'NO'}"
        if ble_inCal:
            mv_txt = "—"
            if isinstance(mv, list) and len(mv) == CH_COUNT and (0 <= ble_cal_ch <= (CH_COUNT - 1)):
                mv_txt = f"{int(mv[ble_cal_ch])}mV"
            cal_txt = f"CAL: {CAL_STAGE_NAMES.get(ble_stage, str(ble_stage))}  ch={ble_cal_ch}  mv={mv_txt}  Q={ble_cal_quality_q}"

        task_score = "—"
        if task_total > 0:
            task_score = f"{task_correct}/{task_total} ({(100.0 * task_correct / task_total):.1f}%)"

        task_state_txt = task_state if app_mode == APP_MODE_TASK else "—"

        pe_avg = current_tap_path_efficiency_avg_pct()

        lines = [
            f"REC: {'ON' if recording else 'OFF'}   (L to toggle)   {os.path.basename(last_rec_path) if last_rec_path else ''}",
            f"MODE: {('POINTING TEST' if app_mode == APP_MODE_TAPPING else 'COMMAND TASK')}   TASK score={task_score}   state={task_state_txt}",
            f"BLE {'OK' if ble_ok else 'STALE'}  seq={ble_seq if ble_seq is not None else '—'}  raw=0b{ble_raw_mask:06b}  cmd=0b{ble_cmd_mask:06b}",
            f"DEV: {dev_name or '—'}  {dev_addr or '—'}  HID={'ON' if ble_hid_connected else 'OFF'}  VALID={'ALL' if ble_all_valid else 'PARTIAL'}  WET={'ON' if ble_wet_lock else 'OFF'}",
            f"BAT: {battery_text(battery_mv, battery_pct, battery_valid)}",
            f"CMD: {CMD_CODE_LABELS.get(ble_cmd_code, '') or 'NONE'}  ratio={' '.join(str(int(v)) for v in ble_ratio) if isinstance(ble_ratio, list) else '—'}",
            "BLE packet not received" if not ble_ok else "",
            cal_txt,
            f"pos x={x_mm:6.1f} y={y_mm:6.1f}  v={spd:6.1f}  heading {(f'{hd:5.1f}°' if hd is not None else '—')}",
            f"MT(avg) {(f'{mt_avg_s:.3f}s' if mt_avg_s is not None else '—')}  TP {(f'{tp:.2f} bits/s' if tp is not None else '—')}",
            f"Path Efficiency(%) {(f'{pe_avg:.2f}' if pe_avg is not None else '—')}",
            "Keys: [ESC]Quit [Q]Fullscreen [R]Reset [+/-]Speed [L]REC [H]HUD [P]Panel [U]Unlock [M]Maze",
        ]

        pads, gap = 8, 4
        surf_lines = [font_s.render(s, True, (30, 30, 30)) for s in lines]
        w = max(s.get_width() for s in surf_lines) + 2 * pads
        h = sum(s.get_height() for s in surf_lines) + (len(lines) - 1) * gap + 2 * pads

        x0, y0 = inner.left + 8, inner.top + 8
        panel = pygame.Surface((w, h), pygame.SRCALPHA)
        panel.fill((255, 255, 255, 190))
        pygame.draw.rect(panel, (180, 180, 180), panel.get_rect(), 1)
        yy = pads
        for s in surf_lines:
            panel.blit(s, (pads, yy))
            yy += s.get_height() + gap
        screen.blit(panel, (x0, y0))

    # ===== 실행 =====
    running = True
    last_touchmask = 0
    last_touchmask_t = 0.0

    set_caption()

    while running:
        nowp = time.perf_counter()

        # ---- BLE 읽기 ----
        with ble_lock:
            ble_seq = ble_state.get("seq", None)
            ble_mask = int(ble_state["mask"]) & 0x3F
            ble_raw_mask = int(ble_state.get("raw_mask", ble_mask)) & 0x3F
            ble_cmd_mask = int(ble_state.get("cmd_mask", ble_mask)) & 0x3F
            ble_cmd_code = int(ble_state.get("cmd_code", 0))
            ble_cal_quality_q = int(ble_state.get("cal_quality_q", 0))
            ble_ratio = list(ble_state.get("ratio", [0] * CH_COUNT))[:CH_COUNT]
            ble_hid_connected = bool(ble_state.get("hid_connected", False))
            ble_all_valid = bool(ble_state.get("all_valid", False))
            ble_wet_lock = bool(ble_state.get("wet_lock", False))
            ble_inCal = bool(ble_state["inCal"])
            ble_cal_ch = int(ble_state["cal_ch"])
            ble_stage  = int(ble_state["stage"])
            mv = ble_state.get("mv", None)
            dev_addr = ble_state.get("addr", None)
            dev_name = ble_state.get("name", None)
            battery_mv = ble_state.get("battery_mv", None)
            battery_pct = ble_state.get("battery_pct", None)
            battery_valid = bool(ble_state.get("battery_valid", False))
            battery_seen_t = float(ble_state.get("battery_seen_t", 0.0) or 0.0)
            last_valid_t = float(ble_state.get("last_valid_t", 0.0) or 0.0)

        if target_lock["addr"] is not None:
            if (nowp - float(target_lock["last_valid_t"] or 0.0)) > LOCK_RELEASE_SEC:
                unlock()

        ble_ok = (nowp - last_valid_t) <= STALE_SEC
        battery_ok = (nowp - battery_seen_t) <= STALE_SEC
        if not battery_ok:
            battery_valid = False

        if not CLICK_CHANNELS_ENABLED:
            ble_raw_mask &= ACTIVE_GUI_MASK
            ble_cmd_mask &= ACTIVE_GUI_MASK
            if ble_cmd_code in (1, 2):
                ble_cmd_code = 0

        board_ready = ble_ok and (not ble_inCal) and (ble_stage == 0) and ble_all_valid
        if board_ready:
            if ready_none_since is None:
                ready_none_since = nowp
        else:
            ready_none_since = None

        if gui_recal_request_pending and ble_inCal:
            gui_recal_started = True
            calibration_required = True
            calibration_done = False
            calibration_armed = True

        if calibration_done and ble_ok:
            if (session_device_addr is not None) and (dev_addr is not None) and (dev_addr != session_device_addr):
                reset_for_new_session("A different board was detected. Calibration is required again.")
            elif ble_inCal and (not prev_ble_inCal):
                if not gui_recal_request_pending:
                    reset_for_new_session("The board rebooted. Calibration is required again.")
            elif (last_seen_seq is not None) and (ble_seq is not None):
                if (100 < last_seen_seq < 60000) and (ble_seq < 20) and (ble_seq + 50 < last_seen_seq):
                    if not gui_recal_request_pending:
                        reset_for_new_session("The board rebooted. Calibration is required again.")

        if ble_ok and (ready_none_since is not None):
            if (nowp - ready_none_since) >= CAL_DONE_CONFIRM_SEC:
                allow_finish = (not gui_recal_request_pending) or gui_recal_started
                if (calibration_armed or (not calibration_done)) and allow_finish:
                    mark_calibration_done(dev_addr=dev_addr, seq=ble_seq)

        prev_ble_inCal = ble_inCal
        if ble_seq is not None:
            last_seen_seq = ble_seq

        if ble_ok:
            last_touchmask = ble_cmd_mask
            last_touchmask_t = nowp
            touch_mask = ble_cmd_mask
            raw_touch_mask = ble_raw_mask
        else:
            touch_mask = last_touchmask if (nowp - last_touchmask_t) <= TOUCH_HOLD_SEC else 0
            raw_touch_mask = 0

        touch_mask |= (sim_touch_mask & 0x3F)
        raw_touch_mask |= (sim_touch_mask & 0x3F)

        mx_now, my_now = pygame.mouse.get_pos()
        gui_cursor_px = int(clamp(mx_now, 0, win_w - 1))
        gui_cursor_py = int(clamp(my_now, 0, win_h - 1))

        if app_mode in (APP_MODE_TAPPING, APP_MODE_TASK):
            old_x_mm, old_y_mm = x_mm, y_mm
            if inner.collidepoint(gui_cursor_px, gui_cursor_py):
                x_mm = clamp((gui_cursor_px - inner.left) * tap_mm_per_px, 0.0, AREA_W_MM)
                y_mm = clamp((gui_cursor_py - inner.top)  * tap_mm_per_px, 0.0, AREA_H_MM)
            if app_mode == APP_MODE_TAPPING and calibration_done:
                d_tap = math.hypot(x_mm - old_x_mm, y_mm - old_y_mm)
                if d_tap > 0.0:
                    tap_path_sub_mm += d_tap
        elif app_mode == APP_MODE_MAZE:
            if inner.collidepoint(gui_cursor_px, gui_cursor_py):
                maze_cursor[0] = (gui_cursor_px - maze_offx) / max(1e-9, maze_scale)
                maze_cursor[1] = (gui_cursor_py - maze_offy) / max(1e-9, maze_scale)

        # ---- timeout (maze) ----
        if app_mode == APP_MODE_MAZE and maze_status == "RUNNING" and trial_t0 is not None:
            if (nowp - trial_t0) >= TRIAL_TIME_LIMIT_SEC:
                end_maze_trial(force_status="TIMEOUT")

        # ---- 이벤트 ----
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False

            elif e.type == pygame.KEYDOWN:
                uni = (getattr(e, "unicode", "") or "").lower()

                if e.key == pygame.K_ESCAPE:
                    running = False

                elif e.key == pygame.K_q:
                    fullscreen = not fullscreen
                    apply_display_mode()
                    set_status("Fullscreen" if fullscreen else "Windowed", 1.0)

                elif e.key == pygame.K_h:
                    SHOW_HUD = not SHOW_HUD

                elif e.key == pygame.K_F1:
                    SHOW_HELP = not SHOW_HELP

                elif e.key == pygame.K_p:
                    SHOW_FPCB_PANEL = not SHOW_FPCB_PANEL
                    rebuild_layout()

                elif e.key == pygame.K_u:
                    unlock()
                    reset_for_new_session("Board lock released. Calibration is required again.")

                elif (e.key == pygame.K_l) or (uni == "l"):
                    if recording:
                        stop_recording()
                    else:
                        start_recording()

                elif e.key == pygame.K_g:
                    set_mode(APP_MODE_TAPPING)
                elif e.key == pygame.K_t:
                    set_mode(APP_MODE_TASK)
                elif e.key == pygame.K_m:
                    set_mode(APP_MODE_MAZE)

                elif e.key in (pygame.K_EQUALS, pygame.K_PLUS):
                    if app_mode == APP_MODE_MAZE:
                        MOUSE_GAIN = min(8.0, MOUSE_GAIN + 0.1)
                    else:
                        tap_speed = min(5.0, tap_speed + 0.1)

                elif e.key == pygame.K_MINUS:
                    if app_mode == APP_MODE_MAZE:
                        MOUSE_GAIN = max(0.1, MOUSE_GAIN - 0.1)
                    else:
                        tap_speed = max(0.2, tap_speed - 0.1)

                elif app_mode in (APP_MODE_TAPPING, APP_MODE_TASK) and e.key == pygame.K_r:
                    tap_pos_hist.clear()
                    prev_hit_time = None
                    mt_sum = 0.0
                    mt_count = 0
                    mt_avg_s = None
                    seq_pos = 0
                    reset_tap_path_metrics(reset_average=True)
                    reset_task_plan(nowp, reset_scores=True)
                    move_gui_cursor_to_tap_center()
                    set_status("Pointing Test / Command TASK reset: moved to center.", 1.2)

                elif app_mode == APP_MODE_MAZE:
                    if e.key == pygame.K_r:
                        reset_maze_trial()
                        move_gui_cursor_to_maze_start()
                        set_status("Maze trial reset: moved to the start point.", 1.0)
                    elif e.key == pygame.K_SPACE:
                        if maze_status != "RUNNING":
                            reset_maze_trial()
                            start_maze_trial()
                    elif e.key == pygame.K_RETURN:
                        if maze_status == "RUNNING":
                            end_maze_trial(force_status="DONE")
                    elif e.key == pygame.K_BACKSPACE:
                        if maze_status == "RUNNING":
                            end_maze_trial(force_status="TIMEOUT")
                    elif e.key == pygame.K_n:
                        if trial < N_TRIALS_PER_PART-1:
                            trial += 1
                            reset_maze_trial()
                    elif e.key == pygame.K_b:
                        if trial > 0:
                            trial -= 1
                            reset_maze_trial()
                    elif e.key == pygame.K_LEFTBRACKET:
                        if participant > 0:
                            participant -= 1
                            reset_maze_trial()
                    elif e.key == pygame.K_RIGHTBRACKET:
                        if participant < N_PARTICIPANTS-1:
                            participant += 1
                            reset_maze_trial()
                    elif pygame.K_1 <= e.key <= pygame.K_6:
                        ch = e.key - pygame.K_1
                        sim_touch_mask ^= (1 << ch)

            elif e.type == pygame.VIDEORESIZE and (not fullscreen):
                windowed_size = (max(960, e.w), max(640, e.h))
                apply_display_mode()

            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                pos = e.pos
                if ui_buttons["cal"]["rect"].collidepoint(pos):
                    request_calibration()
                elif ui_buttons["recal"]["rect"].collidepoint(pos):
                    request_runtime_recalibration()
                elif ui_buttons["tap"]["rect"].collidepoint(pos):
                    set_mode(APP_MODE_TAPPING)
                elif ui_buttons["task"]["rect"].collidepoint(pos):
                    set_mode(APP_MODE_TASK)
                elif ui_buttons["maze"]["rect"].collidepoint(pos):
                    set_mode(APP_MODE_MAZE)

            elif e.type == pygame.MOUSEMOTION:
                gui_cursor_px = int(clamp(e.pos[0], 0, win_w - 1))
                gui_cursor_py = int(clamp(e.pos[1], 0, win_h - 1))

        # ---- Maze arrow move ----
        if app_mode == APP_MODE_MAZE:
            keys = pygame.key.get_pressed()
            step = ARROW_STEP_UNITS / FPS
            if keys[pygame.K_LEFT]:
                maze_cursor[0] -= step
            if keys[pygame.K_RIGHT]:
                maze_cursor[0] += step
            if keys[pygame.K_UP]:
                maze_cursor[1] -= step
            if keys[pygame.K_DOWN]:
                maze_cursor[1] += step

        # ---- speed 계산 ----
        tap_spd, tap_hd = compute_speed_heading_once(tap_pos_hist, x_mm, y_mm, VEL_WINDOW, MIN_SPEED)
        maze_spd, _ = compute_speed_heading_once(maze_pos_hist, maze_cursor[0], maze_cursor[1], VEL_WINDOW, 0.0)

        # ---- 탭핑 로직 ----
        if calibration_done and app_mode == APP_MODE_TAPPING:
            active_ring_idx = seq_indices[seq_pos]
            tx_mm, ty_mm = targets_mm[active_ring_idx]
            if math.hypot(x_mm - tx_mm, y_mm - ty_mm) <= CENTER_HIT_MM:
                nowt = time.perf_counter()
                pe_hit = finish_current_tap_path_segment()
                # Store the actual per-target Path Efficiency only when the
                # target is completed. This prevents very large transient PE
                # values at the beginning of a movement from being logged.
                write_log_row(
                    x_mm, y_mm, tap_spd, touch_mask, current_tap_tp(), mv,
                    mode_name=current_log_mode_name(),
                    event_name="target_hit",
                    path_eff_pct=pe_hit
                )
                if prev_hit_time is not None:
                    dt = nowt - prev_hit_time
                    mt_sum += dt
                    mt_count += 1
                    mt_avg_s = mt_sum / mt_count
                prev_hit_time = nowt
                seq_pos = (seq_pos + 1) % TARGET_COUNT
                start_next_tap_path_segment()

        # ---- TASK 로직 ----
        if app_mode == APP_MODE_TASK:
            schedule_next_task(nowp)
            # 22 kΩ 튜닝 후에는 GUI의 raw highlight와 실제 HID cmd 판정이 다를 수 있다.
            # Command TASK는 실제 마우스 이동과 같은 cmd mask 기준으로 평가한다.
            raw_task_mask = touch_mask if ble_ok else 0
            raw_task_mask |= (sim_touch_mask & 0x3F)

            if calibration_done and task_current_idx is not None:
                if task_state == TASK_STATE_WAIT_RELEASE:
                    if raw_task_mask == 0:
                        if task_release_zero_since is None:
                            task_release_zero_since = nowp
                        elif (nowp - task_release_zero_since) >= TASK_RELEASE_STABLE_SEC:
                            task_state = TASK_STATE_WAIT_RESPONSE
                            task_response_deadline = nowp + TASK_TIMEOUT_SEC
                            task_prev_input_mask = 0
                            task_candidate_mask = 0
                            task_hold_start = 0.0
                    else:
                        task_release_zero_since = None

                elif task_state == TASK_STATE_WAIT_RESPONSE:
                    if nowp >= task_response_deadline:
                        task_total += 1
                        task_feedback = "No response"
                        task_feedback_color = (200, 60, 60)
                        task_feedback_until = nowp + TASK_FEEDBACK_SEC

                        req = TASK_ITEMS[task_current_idx]
                        mv_snapshot = list(mv) if isinstance(mv, list) else None
                        pending_response_log = {
                            "requested_command": req["label"],
                            "actual_command": "No response",
                            "x": x_mm,
                            "y": y_mm,
                            "speed": tap_spd,
                            "tp": current_tap_tp(),
                            "touch_mask": 0,
                            "mv": mv_snapshot,
                        }

                        task_last_idx = task_current_idx
                        task_current_idx = None
                        task_state = TASK_STATE_IDLE
                        task_next_prompt_at = nowp + TASK_REST_SEC
                        task_prev_input_mask = 0
                        task_candidate_mask = 0
                        task_hold_start = 0.0
                    else:
                        is_touch_rising = (
                            task_prev_input_mask == 0 and
                            raw_task_mask != 0
                        )

                        if is_touch_rising:
                            task_candidate_mask = raw_task_mask
                            task_hold_start = nowp
                            task_state = TASK_STATE_WAIT_HOLD
                            task_prev_input_mask = raw_task_mask
                        else:
                            task_prev_input_mask = raw_task_mask

                elif task_state == TASK_STATE_WAIT_HOLD:
                    if nowp >= task_response_deadline:
                        task_total += 1
                        task_feedback = "No response"
                        task_feedback_color = (200, 60, 60)
                        task_feedback_until = nowp + TASK_FEEDBACK_SEC

                        req = TASK_ITEMS[task_current_idx]
                        mv_snapshot = list(mv) if isinstance(mv, list) else None
                        pending_response_log = {
                            "requested_command": req["label"],
                            "actual_command": "No response",
                            "x": x_mm,
                            "y": y_mm,
                            "speed": tap_spd,
                            "tp": current_tap_tp(),
                            "touch_mask": 0,
                            "mv": mv_snapshot,
                        }

                        task_last_idx = task_current_idx
                        task_current_idx = None
                        task_state = TASK_STATE_IDLE
                        task_next_prompt_at = nowp + TASK_REST_SEC
                        task_prev_input_mask = 0
                        task_candidate_mask = 0
                        task_hold_start = 0.0
                    elif raw_task_mask == 0:
                        task_state = TASK_STATE_WAIT_RESPONSE
                        task_prev_input_mask = 0
                        task_candidate_mask = 0
                        task_hold_start = 0.0
                    elif raw_task_mask != task_candidate_mask:
                        task_candidate_mask = raw_task_mask
                        task_hold_start = nowp
                        task_prev_input_mask = raw_task_mask
                    else:
                        task_prev_input_mask = raw_task_mask
                        if (nowp - task_hold_start) >= TASK_TOUCH_HOLD_SEC:
                            req = TASK_ITEMS[task_current_idx]
                            act_cmd = mask_to_command(task_candidate_mask)
                            actual_mask = task_candidate_mask
                            is_correct = (actual_mask == (1 << req["ch"]))

                            task_total += 1
                            if is_correct:
                                task_correct += 1
                                task_feedback = f"Correct: {act_cmd}"
                                task_feedback_color = (30, 140, 60)
                            else:
                                shown_cmd = act_cmd if act_cmd else "None"
                                task_feedback = f"Wrong: {shown_cmd}"
                                task_feedback_color = (200, 60, 60)
                            task_feedback_until = nowp + TASK_FEEDBACK_SEC

                            mv_snapshot = list(mv) if isinstance(mv, list) else None
                            pending_response_log = {
                                "requested_command": req["label"],
                                "actual_command": act_cmd if act_cmd else "None",
                                "x": x_mm,
                                "y": y_mm,
                                "speed": tap_spd,
                                "tp": current_tap_tp(),
                                "touch_mask": task_candidate_mask,
                                "mv": mv_snapshot,
                            }

                            task_last_idx = task_current_idx
                            task_current_idx = None
                            task_state = TASK_STATE_IDLE
                            task_next_prompt_at = nowp + TASK_REST_SEC
                            task_prev_input_mask = 0
                            task_candidate_mask = 0
                            task_hold_start = 0.0
                else:
                    task_prev_input_mask = raw_task_mask
            else:
                task_prev_input_mask = raw_task_mask
        else:
            task_prev_input_mask = 0
            task_candidate_mask = 0
            task_hold_start = 0.0
            if task_state != TASK_STATE_IDLE:
                task_state = TASK_STATE_IDLE

        # ---- Maze 로직 ----
        if app_mode == APP_MODE_MAZE:
            cur_pos = (maze_cursor[0], maze_cursor[1])
            if maze_status == "RUNNING":
                din, dout = accumulate_in_out(maze_last_pos, cur_pos, V, maze_Wv)
                dist_in += din
                dist_out += dout
            maze_last_pos = cur_pos

            inside_now = point_inside_variable_width(maze_cursor[0], maze_cursor[1], V, maze_Wv)

            if maze_status == "RUNNING" and inside_now:
                d, idx, tt, s_cur, w_cur = closest_s_on_polyline(
                    maze_cursor[0], maze_cursor[1], V, maze_Wv, maze_seg_len, maze_s_at_v
                )
                jump_max = max(PROGRESS_JUMP_MAX_BASE_UNITS, MOUSE_GAIN * PROGRESS_JUMP_PER_GAIN)
                if (s_cur >= (progress_s - PROGRESS_BACKTRACK_ALLOW)) and ((s_cur - progress_s) <= jump_max):
                    progress_s = max(progress_s, s_cur)

            if maze_status == "RUNNING":
                ex, ey = V[-1]
                end_r = max(maze_Wv[-1]*0.75, 20.0)
                near_end = (math.hypot(maze_cursor[0]-ex, maze_cursor[1]-ey) <= end_r)
                progressed_enough = (progress_s >= (maze_total_geom_len - END_PROGRESS_MARGIN_UNITS))
                if inside_now and near_end and progressed_enough:
                    end_maze_trial(force_status="DONE")

        # ---- 렌더 ----
        if app_mode == APP_MODE_MAZE:
            draw_maze_scene(raw_touch_mask, touch_mask, ble_ok, ble_seq, ble_inCal, ble_cal_ch, ble_stage, mv, ble_ratio, dev_name, ble_cmd_code, ble_cal_quality_q, ble_hid_connected, ble_all_valid, ble_wet_lock, battery_mv, battery_pct, battery_valid)
        else:
            screen.fill((240, 242, 245))
            pygame.draw.rect(screen, (70, 72, 76), inner, 2)
            if app_mode == APP_MODE_TAPPING:
                draw_tapping_scene()
            elif app_mode == APP_MODE_TASK:
                draw_task_panel()

        draw_fpcb_panel(raw_touch_mask, touch_mask, ble_inCal, ble_cal_ch, ble_stage, mv, ble_ratio, ble_cal_quality_q, ble_cmd_code, ble_wet_lock)
        draw_top_controls()
        draw_rec_badge()

        if app_mode == APP_MODE_MAZE:
            # Maze는 자체 HUD를 그림
            pass
        else:
            draw_common_hud(
                tap_spd, tap_hd, ble_ok, ble_seq, raw_touch_mask, touch_mask, ble_cmd_code,
                ble_inCal, ble_cal_ch, ble_stage, mv, ble_ratio, ble_cal_quality_q,
                ble_hid_connected, ble_all_valid, ble_wet_lock, dev_name, dev_addr,
                battery_mv, battery_pct, battery_valid
            )

        if calibration_required and not calibration_done:
            draw_calibration_blocker()

        draw_status_bar()
        draw_gui_cursor()

        # ---- 로깅 ----
        if app_mode == APP_MODE_MAZE:
            maybe_log(
                maze_spd,
                touch_mask,
                mv,
                maze_cursor[0],
                maze_cursor[1],
                current_maze_tp(nowp)
            )
        else:
            maybe_log(
                tap_spd,
                touch_mask,
                mv,
                x_mm,
                y_mm,
                current_tap_tp()
            )

        clock.tick(FPS)
        pygame.display.flip()

    if recording:
        stop_recording()
    pygame.quit()
    sys.exit(0)

if __name__ == "__main__":
    main()