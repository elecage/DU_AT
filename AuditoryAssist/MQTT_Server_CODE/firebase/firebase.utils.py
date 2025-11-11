# firebase/firebase_utils.py
import os
import firebase_admin
from firebase_admin import credentials, messaging
from firebase_admin import exceptions as fae

# 경로 상수
TOKENS_PATH = "/home/mqtt/MQTTpr/firebase/tokens.txt"
KEY_PATH    = "/home/mqtt/MQTTpr/firebase/pushalret-firebase-adminsdk-fbsvc-46471ca856.json"

# 안드로이드 알림 채널(앱과 동일해야 함)
ANDROID_CHANNEL_ID = "alerts"

def initialize_firebase():
    if not firebase_admin._apps:
        if not os.path.exists(KEY_PATH):
            raise FileNotFoundError(f"❌ Firebase 키 파일 없음: {KEY_PATH}")
        cred = credentials.Certificate(KEY_PATH)
        firebase_admin.initialize_app(cred)
        print("✅ Firebase Admin 초기화 완료")

def load_fcm_tokens(file_path=TOKENS_PATH):
    if not os.path.exists(file_path):
        return []
    with open(file_path, "r") as f:
        return [line.strip() for line in f if line.strip()]

def save_fcm_token(token, file_path=TOKENS_PATH):
    tokens = load_fcm_tokens(file_path)
    if token not in tokens:
        with open(file_path, "a") as f:
            f.write(token + "\n")
        print(f"✅ FCM 토큰 저장됨: {token}")
    else:
        print(f"ℹ️ 이미 등록된 토큰: {token}")

def remove_fcm_token(bad_token, file_path=TOKENS_PATH):
    """유효하지 않은(만료/등록해제) 토큰을 파일에서 제거"""
    if not os.path.exists(file_path):
        return
    tokens = load_fcm_tokens(file_path)
    new_tokens = [t for t in tokens if t != bad_token]
    if len(new_tokens) != len(tokens):
        with open(file_path, "w") as f:
            f.write("\n".join(new_tokens) + ("\n" if new_tokens else ""))
        print(f"🧹 무효 토큰 제거: {bad_token}")

def send_fcm_messages(title, body, token_file=TOKENS_PATH):
    """
    안드로이드 채널/우선순위/사운드 + data 포함 전송
    - 백그라운드: 시스템 알림 표시
    - 포그라운드: 앱의 onMessageReceived() 호출 → 로컬 알림 표시 가능
    - 무효 토큰은 자동 정리
    """
    initialize_firebase()
    tokens = load_fcm_tokens(token_file)
    if not tokens:
        print("⚠️ 전송할 토큰이 없습니다.")
        return

    android_cfg = messaging.AndroidConfig(
        priority='high',
        notification=messaging.AndroidNotification(
            channel_id=ANDROID_CHANNEL_ID,
            sound='default',
        ),
        ttl=3600,  # 1시간
    )

    common_data = {
        "via": "mqtt_server",
        "title": title,
        "body": body,
        # 상황에 따라 "type": "ai_fire|shz|mq5|mq7|all_true|water|doorbell" 추가 가능
    }

    for token in tokens:
        msg = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            android=android_cfg,
            data=common_data,
            token=token,
        )
        try:
            resp = messaging.send(msg)
            print(f"📤 FCM 전송 성공: {token} → {resp}")
        except fae.FirebaseError as e:
            # 대표적인 만료/등록해제 케이스 제거
            code_s = getattr(e, "code", None)
            msg_s  = str(e)
            if (code_s and str(code_s).upper() in ("UNREGISTERED", "INVALID_ARGUMENT")) \
               or ("not registered" in msg_s.lower()) \
               or ("unregistered" in msg_s.lower()):
                print(f"❌ 무효/만료 토큰: {token} → 자동 제거")
                remove_fcm_token(token, token_file)
            else:
                print(f"❌ FCM 전송 실패 ({token}): {e}")
        except Exception as e:
            print(f"❌ FCM 전송 실패 ({token}): {e}")
