"""Public deployment setting. Store the bot username here, never its token."""
import re

# GitHub에는 빈 문자열로 둡니다. 설치할 Pi에서 BotFather의 @없는 사용자명을 입력합니다.
# 표시 이름이나 Bot Token을 넣지 마세요.
BOT_USERNAME = ""


def get_bot_username():
    value = BOT_USERNAME
    if (not isinstance(value, str) or not value
            or re.fullmatch(r"[A-Za-z0-9_]+", value) is None):
        raise ValueError("TELEGRAM_BOT_USERNAME_NOT_CONFIGURED")
    return value.lower()
