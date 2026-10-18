"""Standard-library Telegram client; never include tokens, URLs or bodies in errors."""
from __future__ import annotations
from telegram_settings import get_bot_username

import json
import re
import ssl
import urllib.error
import urllib.request


class TelegramError(RuntimeError):
    def __init__(self, code, *, retryable=False, retry_after=0, fatal=False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after
        self.fatal = fatal

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class TelegramClient:
    def __init__(self, token, opener=None):
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
            raise TelegramError("invalid_token_format", fatal=True)
        self._token = token
        self._opener = opener or urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context()), NoRedirect())

    def _raise_api_error(self, code, body, method=None):
        if type(code) is not int:
            raise TelegramError("invalid_api_response", retryable=True)
        if (method == 'editMessageReplyMarkup' and code == 400
                and 'message is not modified' in str(body.get('description', '')).lower()):
            raise TelegramError('message_not_modified') from None
        parameters = body.get('parameters')
        delay = parameters.get('retry_after', 0) if isinstance(parameters, dict) else 0
        if type(delay) is not int or delay < 0:
            delay = 0
        raise TelegramError(f"http_{code}", retryable=(code == 429 or code >= 500),
                            retry_after=delay, fatal=(code == 401)) from None

    def request(self, method, payload=None):
        if method not in {'getMe', 'getChat', 'sendMessage', 'getUpdates', 'getWebhookInfo', 'answerCallbackQuery', 'editMessageReplyMarkup'}:
            raise ValueError("Unsupported method")
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self._token}/{method}",
            data=json.dumps(payload or {}, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with self._opener.open(request, timeout=25 if method == 'getUpdates' else 15) as response:
                raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise TelegramError("oversized_response", retryable=True)
            body = json.loads(raw)
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read(65536))
                if not isinstance(body, dict):
                    body = {}
            except (OSError, ValueError):
                body = {}
            finally:
                exc.close()
            self._raise_api_error(exc.code, body, method)
        except (OSError, ValueError):
            # A timeout during sendMessage does not prove that no message was sent.
            raise TelegramError("network_or_response_ambiguous", retryable=True) from None
        if not isinstance(body, dict):
            raise TelegramError("invalid_api_response", retryable=True)
        if body.get('ok') is not True:
            self._raise_api_error(body.get('error_code'), body, method)
        if method == 'answerCallbackQuery':
            if body.get('result') is not True:
                raise TelegramError('invalid_api_result', retryable=True)
            return True
        if not isinstance(body.get('result'), list if method == 'getUpdates' else dict):
            raise TelegramError("invalid_api_result", retryable=True)
        return body['result']

    def edit_markup(self, chat_id, message_id, markup):
        result = self.request('editMessageReplyMarkup', {
            'chat_id': chat_id, 'message_id': message_id, 'reply_markup': markup})
        if result.get('message_id') != message_id or result.get('chat', {}).get('id') != chat_id:
            raise TelegramError('edit_result_ambiguous', retryable=True)

    def verify_bot(self):
        try:
            expected_bot = get_bot_username()
        except ValueError:
            raise TelegramError('bot_username_not_configured', fatal=True) from None
        bot = self.request('getMe')
        if bot.get('is_bot') is not True or bot.get('username', '').lower() != expected_bot:
            raise TelegramError("unexpected_bot", fatal=True)
        if bot.get('can_join_groups') is not False:
            raise TelegramError("group_join_not_disabled", fatal=True)

    def verify_private_chat(self, chat_id):
        chat = self.request('getChat', {'chat_id': chat_id})
        if chat.get('type') != 'private' or chat.get('id') != chat_id:
            raise TelegramError("unexpected_private_chat", fatal=True)

    def send(self, chat_id, text, *, reply_markup=None):
        payload = {
            'chat_id': chat_id, 'text': text,
            'disable_notification': False,
            'link_preview_options': {'is_disabled': True},
        }
        if reply_markup is not None:
            payload['reply_markup'] = reply_markup
        result = self.request('sendMessage', payload)
        message_id = result.get('message_id')
        if (result.get('chat', {}).get('id') != chat_id or
                type(message_id) is not int or message_id <= 0):
            raise TelegramError("send_result_ambiguous", retryable=True)
        return message_id
