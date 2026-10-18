"""Mobile single-operator handover, inside OperatorStore's gate and transaction.

The existing operator remains authorized until an atomic final confirmation.
No BotFather ownership, bot token, SSH account or Telegram history is changed.
Official API: https://core.telegram.org/bots/features#command-scopes
Deep links: https://core.telegram.org/bots/features#deep-linking
"""
from telegram_settings import get_bot_username
import hashlib
import hmac
import re
import secrets
from notification_store import utc_now

HANDOVER_TTL = 900
HANDOVER_SCHEMA = """
CREATE TABLE IF NOT EXISTS operator_handover (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 request_id TEXT NOT NULL, owner_generation TEXT NOT NULL,
 token_hash TEXT NOT NULL, label TEXT NOT NULL,
 created_at REAL NOT NULL, expires_at REAL NOT NULL,
 candidate_chat_id INTEGER CHECK(candidate_chat_id>0),
 salt TEXT, code_hash TEXT,
 attempts INTEGER NOT NULL DEFAULT 0,
 verified INTEGER NOT NULL DEFAULT 0 CHECK(verified IN (0,1))
) STRICT;
"""


class HandoverMixin:
    def _handover_feedback(self, c, actor, text, now):
        recent = c.execute("SELECT 1 FROM guardian_replies WHERE chat_id=? AND dedupe_key LIKE 'handover:feedback:%' AND created_at>?",
                           (actor, now - 10)).fetchone()
        if not recent:
            self._reply(c, 'handover:feedback:' + secrets.token_hex(16), actor, text, now, expiry=now + 300)

    @staticmethod
    def _clear_handover(c):
        c.execute("DELETE FROM guardian_replies WHERE dedupe_key LIKE 'handover:%'")
        c.execute("DELETE FROM operator_actions WHERE kind LIKE 'handover_%'")
        c.execute("DELETE FROM operator_state WHERE kind LIKE 'handover_%'")
        c.execute('DELETE FROM operator_handover')

    def _expire_handover(self, c, now):
        row = c.execute('SELECT * FROM operator_handover WHERE singleton=1').fetchone()
        operator = self._operator(c)
        if row and (row['expires_at'] <= now or row['attempts'] >= 5 or not operator
                    or row['owner_generation'] != operator['generation']):
            self._clear_handover(c)

    def _handover_row(self, c, now, request_id=None):
        self._expire_handover(c, now)
        row = c.execute('SELECT * FROM operator_handover WHERE singleton=1').fetchone()
        return row if row and (request_id is None or row['request_id'] == request_id) else None

    def _handover_notice(self, c, row, text, now, rows=None):
        self._notice(c, text, now, rows=rows, expires=row['expires_at'],
                     key='handover:operator:' + secrets.token_hex(16))

    def _handover_status(self, c, row, now):
        request = row['request_id']
        buttons = []
        if row['verified']:
            return self._handover_final_prompt(c, row, now)
        if row['candidate_chat_id']:
            status = '새 운영자가 교체를 신청했습니다.\n새 운영자에게 확인 숫자 6자리를 직접 확인해주세요.'
            buttons.append([self._button(c, '새 운영자 확인 숫자 입력', 'handover_code', request, now, row['expires_at'])])
        else:
            status = '새 운영자가 링크를 열고 시작하기를 기다리고 있습니다.\n링크를 잃어버렸다면 교체를 취소하고 다시 시작하세요.'
        buttons.append([self._button(c, '교체 취소', 'handover_cancel', request, now, row['expires_at'])])
        buttons.append(self._home_button(c, now))
        self._handover_notice(c, row, f"[운영자 교체 대기]\n새 운영자 별칭: {row['label']}\n{status}\n"
                              '교체 완료 전까지 현재 운영자의 권한은 유지됩니다.', now, buttons)

    def _handover_final_prompt(self, c, row, now):
        # A newly displayed final confirmation supersedes every prior final button.
        c.execute("DELETE FROM operator_actions WHERE kind='handover_confirm'")
        old = self._operator(c)['chat_id']
        routes = c.execute('SELECT count(*) FROM notification_recipients WHERE chat_id=?', (old,)).fetchone()[0]
        other = c.execute('SELECT count(*) FROM notification_recipients WHERE chat_id!=? AND enabled=1', (old,)).fetchone()[0]
        warning = '\n교체 후 호출 알림 수신 연결이 0개입니다. 새 운영자의 보호자 등록도 필요합니다.' if not other else ''
        text = (f"[운영자 교체 최종 확인]\n새 운영자 별칭: {row['label']}\n확인 숫자가 일치했습니다.\n"
                f'완료하면 현재 계정의 운영 권한과 보호자 수신 등록 {routes}건 및 관련 서버 정보를 삭제합니다.\n'
                '다른 보호자와 호출 기록은 유지됩니다. 새 운영자는 기존 보호자 정보도 관리할 수 있습니다.\n'
                '새 계정에는 운영 권한만 부여하며 호출 알림 수신은 보호자 등록이 필요합니다.\n'
                '이미 도착한 메시지와 과거 백업은 자동 삭제되지 않습니다.' + warning)
        self._handover_notice(c, row, text, now, [
            [self._button(c, '교체 완료 및 내 등록 정보 삭제', 'handover_confirm', row['request_id'], now, min(now + 120, row['expires_at']))],
            [self._button(c, '교체 취소', 'handover_cancel', row['request_id'], now, row['expires_at'])]])

    def _handover_callback(self, c, kind, target, now):
        row = self._handover_row(c, now)
        if kind == 'handover':
            if row:
                self._handover_status(c, row, now)
            else:
                self._notice(c, '[운영자 교체]\n새 운영자에게 15분 유효 링크를 전달하고, 확인 숫자를 확인한 뒤 최종 승인합니다.\n'
                             '교체 완료 시 현재 계정의 운영 권한과 보호자 수신 등록·관련 서버 정보를 삭제합니다.\n'
                             '신뢰하는 다른 Telegram 계정으로 교체할 때 사용하세요.', now,
                             rows=[[self._button(c, '교체 시작', 'handover_begin', '', now)], self._home_button(c, now)])
        elif kind == 'handover_begin':
            if row:
                self._handover_status(c, row, now)
            else:
                self._set_state(c, 'handover_alias', '', now)
                self._notice(c, '새 운영자의 별칭을 입력해주세요. (1~40자)\n취소: /cancel', now)
        elif not row or target != row['request_id']:
            self._menu(c, now, '이미 취소했거나 만료된 운영자 교체 요청입니다. 현재 운영 권한은 유지됩니다.')
        elif kind == 'handover_cancel':
            self._clear_handover(c)
            self._menu(c, now, '운영자 교체를 취소했습니다. 현재 운영 권한과 수신 등록은 유지됩니다.')
        elif kind == 'handover_code' and row['candidate_chat_id'] and not row['verified']:
            self._set_state(c, 'handover_code', target, now, row['expires_at'])
            self._handover_notice(c, row, f"새 운영자 별칭: {row['label']}\n"
                                  '통화 또는 대면으로 새 운영자의 화면에 있는 CareCall 확인 숫자 6자리를 확인하고 입력해주세요.\n'
                                  'Telegram 로그인 인증번호가 아닙니다. 취소: /cancel', now)
        elif kind == 'handover_confirm' and row['verified'] and row['candidate_chat_id']:
            self._finish_handover(c, row, now)
            return 'handover_complete'

    def _handover_input(self, c, state, text, now):
        if state['kind'] == 'handover_alias':
            if not text or len(text) > 40 or text.startswith('/') or not all(x.isprintable() for x in text):
                self._notice(c, '별칭은 줄바꿈 없이 1~40자로 입력해주세요. 취소: /cancel', now)
                return
            if self._handover_row(c, now):
                self._menu(c, now, '진행 중인 운영자 교체가 있습니다. 운영자 교체 메뉴에서 확인해주세요.')
                return
            token, request = secrets.token_urlsafe(32), secrets.token_hex(16)
            c.execute('''INSERT INTO operator_handover
                (singleton,request_id,owner_generation,token_hash,label,created_at,expires_at)
                VALUES(1,?,?,?,?,?,?)''', (request, self._operator(c)['generation'],
                    hashlib.sha256(token.encode()).hexdigest(), text, now, now + HANDOVER_TTL))
            c.execute('DELETE FROM operator_state')
            row = self._handover_row(c, now)
            link = f'https://t.me/{get_bot_username()}?start=transfer_' + token
            self._handover_notice(c, row, f'[새 운영자 초대]\n별칭: {text}\n유효기간: 지금부터 15분\n{link}\n\n'
                                  '이 링크를 새 운영자에게만 전달해주세요. 링크를 열고 시작하면 운영자 교체 신청이 접수됩니다.\n'
                                  '현재 운영자가 확인 숫자를 확인하고 최종 승인해야 권한이 변경됩니다.', now,
                                  [[self._button(c, '교체 취소', 'handover_cancel', request, now, row['expires_at'])]])
        elif state['kind'] == 'handover_code':
            row = self._handover_row(c, now, state['target'])
            if not row or not row['candidate_chat_id'] or row['verified']:
                c.execute('DELETE FROM operator_state')
                self._menu(c, now, '만료되었거나 처리한 교체 요청입니다. 운영자 교체 메뉴에서 다시 확인해주세요.')
                return
            digest = hashlib.sha256((row['salt'] + text).encode()).hexdigest()
            if not re.fullmatch('[0-9]{6}', text) or not hmac.compare_digest(row['code_hash'], digest):
                attempts = row['attempts'] + 1
                c.execute('UPDATE operator_handover SET attempts=? WHERE singleton=1', (attempts,))
                if attempts >= 5:
                    self._clear_handover(c)
                    self._menu(c, now, '확인 숫자가 5회 일치하지 않아 교체를 취소했습니다. 현재 운영 권한은 유지됩니다.')
                else:
                    self._handover_notice(c, row, f'확인 숫자가 일치하지 않습니다. ({attempts}/5회)\n새 운영자와 다시 확인해주세요. 취소: /cancel', now)
                return
            c.execute('UPDATE operator_handover SET verified=1,code_hash=NULL,salt=NULL WHERE singleton=1')
            c.execute('DELETE FROM operator_state')
            c.execute("DELETE FROM guardian_replies WHERE dedupe_key LIKE 'handover:code:%'")
            self._handover_final_prompt(c, self._handover_row(c, now), now)

    def _handover_message(self, c, message, actor, now):
        text = message['text'].strip()
        match = re.fullmatch(r'/start(?:@' + re.escape(get_bot_username()) + r')? transfer_([A-Za-z0-9_-]{43})', text)
        row = self._handover_row(c, now)
        if text == '/cancel' and row and row['candidate_chat_id'] == actor:
            self._clear_handover(c)
            self._menu(c, now, '새 운영자가 교체 신청을 취소했습니다. 현재 운영 권한은 유지됩니다.')
            self._handover_feedback(c, actor, '운영자 교체 신청을 취소했습니다. 이 계정에 운영 권한은 부여되지 않았습니다.', now)
            return True
        if not match:
            return False
        # Invalid, self, copied-after-claim, stale and exhausted invitations grant nothing.
        if (not row or actor == self._operator(c)['chat_id'] or row['candidate_chat_id'] not in (None, actor)
                or not hmac.compare_digest(row['token_hash'], hashlib.sha256(match[1].encode()).hexdigest())):
            self._handover_feedback(c, actor, '사용할 수 없거나 만료된 운영자 교체 링크입니다.\n현재 운영자는 새 운영자 계정에 링크를 전달해주세요. 도움이 필요하면 기존 운영자에게 문의하세요.', now)
            return True
        if row['candidate_chat_id'] is None:
            code, salt = f'{secrets.randbelow(1000000):06d}', secrets.token_hex(16)
            c.execute('UPDATE operator_handover SET candidate_chat_id=?,salt=?,code_hash=? WHERE singleton=1',
                      (actor, salt, hashlib.sha256((salt + code).encode()).hexdigest()))
            self._reply(c, 'handover:code:' + row['request_id'], actor,
                        'CareCall 운영자 교체를 신청했습니다.\n운영자가 되면 보호자의 등록·해제를 관리하게 됩니다.\n'
                        f'운영자 교체 확인 숫자: {code}\n'
                        '기존 운영자와 통화 또는 대면으로 확인한 뒤 이 숫자를 알려주세요. 최종 승인 전에는 운영 권한이 없습니다.\n'
                        '본인이 신청한 것이 아니거나 동의하지 않으면 /cancel 을 보내세요.', now, expiry=row['expires_at'])
            self._handover_status(c, self._handover_row(c, now), now)
        return True

    def _finish_handover(self, c, row, now):
        """Same SQLite transaction as authorization, one-use callback and update cursor."""
        operator = self._operator(c)
        old, new = operator['chat_id'], row['candidate_chat_id']
        if (old == new or row['owner_generation'] != operator['generation']
                or row['expires_at'] <= now or not row['verified']):
            raise RuntimeError('Handover preconditions changed')
        others = c.execute('SELECT count(*) FROM notification_recipients WHERE chat_id!=?', (old,)).fetchone()[0]
        calls = c.execute('SELECT count(*) FROM call_events').fetchone()[0]
        c.execute('''DELETE FROM guardian_audit WHERE invite_id IN
            (SELECT id FROM guardian_invites WHERE chat_id=?) OR recipient_id IN
            (SELECT rowid FROM notification_recipients WHERE chat_id=?)''', (old, old))
        c.execute('''DELETE FROM guardian_replies WHERE chat_id=? OR admin_reply=1
            OR dedupe_key LIKE 'bootstrap:%' OR invite_id IN
            (SELECT id FROM guardian_invites WHERE chat_id=?)''', (old, old))
        c.execute('DELETE FROM notification_outbox WHERE chat_id=?', (old,))
        c.execute('DELETE FROM guardian_invites WHERE chat_id=?', (old,))
        c.execute('DELETE FROM notification_recipients WHERE chat_id=?', (old,))
        c.execute('DELETE FROM operator_bootstrap')
        c.execute('DELETE FROM operator_actions')
        c.execute('DELETE FROM operator_state')
        self._clear_handover(c)
        c.execute('UPDATE operator_config SET chat_id=?,generation=?,created_at=? WHERE singleton=1',
                  (new, secrets.token_hex(16), now))
        c.execute("INSERT INTO guardian_audit(action,created_at) VALUES('operator_handover_completed',?)", (utc_now(),))
        for table in ('operator_config', 'operator_bootstrap', 'notification_recipients',
                      'notification_outbox', 'guardian_invites', 'guardian_replies'):
            if c.execute(f'SELECT 1 FROM {table} WHERE chat_id=?', (old,)).fetchone():
                raise RuntimeError('Old operator cleanup verification failed')
        if (c.execute('SELECT count(*) FROM notification_recipients').fetchone()[0] != others
                or c.execute('SELECT count(*) FROM call_events').fetchone()[0] != calls
                or c.execute('PRAGMA foreign_key_check').fetchone() is not None):
            raise RuntimeError('Handover preservation verification failed')
        self._menu(c, now, '운영자 교체가 완료되었습니다.\n이 계정에서 보호자를 관리할 수 있습니다.\n'
                   '호출 알림도 받으려면 이 계정의 보호자 수신 등록을 확인해주세요.')
