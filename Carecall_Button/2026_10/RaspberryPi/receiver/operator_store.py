"""Private operator workflow. Updates, authorization and mutations commit together.

The trusted local account bootstraps/recovers the operator; mobile handover is verified in two phases.
Callback data contains an expiring one-use reference, never a chat ID or command.
"""
from __future__ import annotations
from telegram_settings import get_bot_username
from contextlib import closing
from datetime import datetime
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from zoneinfo import ZoneInfo
from delivery_gate import delivery_gate
from guardian_store import GuardianStore, valid_chat
from notification_store import utc_now
from device_registry import default_set
from operator_handover import HandoverMixin, HANDOVER_SCHEMA
from confirmation_store import apply_callback as apply_call_callback

TTL = 900
PAGE_SIZE = 8
SCHEMA = """
CREATE TABLE IF NOT EXISTS operator_config (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 chat_id INTEGER NOT NULL CHECK(chat_id>0),
 generation TEXT NOT NULL, created_at REAL NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS operator_bootstrap (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 token_hash TEXT NOT NULL, expires_at REAL NOT NULL,
 chat_id INTEGER, code_hash TEXT, salt TEXT,
 attempts INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE TABLE IF NOT EXISTS operator_actions (
 nonce TEXT PRIMARY KEY NOT NULL, kind TEXT NOT NULL,
 target TEXT NOT NULL, expires_at REAL NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS operator_state (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 kind TEXT NOT NULL, target TEXT NOT NULL,
 created_at REAL NOT NULL, expires_at REAL NOT NULL
) STRICT;
CREATE UNIQUE INDEX IF NOT EXISTS recipient_management_key
 ON notification_recipients(management_key);
CREATE TRIGGER IF NOT EXISTS recipient_management_key_insert
 AFTER INSERT ON notification_recipients WHEN NEW.management_key IS NULL
 BEGIN
 UPDATE notification_recipients SET management_key=lower(hex(randomblob(16)))
 WHERE set_id=NEW.set_id AND chat_id=NEW.chat_id;
 END;
"""


def private_actor(message):
    if not isinstance(message, dict):
        return None
    chat, sender = message.get('chat'), message.get('from')
    if not isinstance(chat, dict) or not isinstance(sender, dict):
        return None
    chat_id = chat.get('id')
    if (chat.get('type') == 'private' and valid_chat(chat_id)
            and type(sender.get('id')) is int and sender['id'] == chat_id
            and sender.get('is_bot') is False):
        return chat_id
    return None


def fresh_message(message, now):
    stamp = message.get('date')
    return (type(stamp) is int and now - TTL <= stamp <= now + 60
            and not message.get('forward_origin') and not message.get('via_bot'))


class OperatorStore(HandoverMixin, GuardianStore):
    def connect(self):
        c = super().connect()
        c.execute('PRAGMA secure_delete=ON')
        return c

    def approve(self, request_id, code, now=None, *, connection=None):
        if connection is None:
            with delivery_gate(self.path), self.transaction() as c:
                return self.approve(request_id, code, now, connection=c)
        super().approve(request_id, code, now, connection=connection)
        connection.execute('UPDATE guardian_invites SET check_code=NULL WHERE id=?', (request_id,))
        connection.execute("DELETE FROM guardian_replies WHERE invite_id=? AND dedupe_key NOT LIKE 'approved:%'", (request_id,))

    def revoke(self, request_id):
        with delivery_gate(self.path):
            super().revoke(request_id)

    def migrate(self):
        super().migrate()
        with self.transaction() as c:
            additions = {
                'notification_recipients': [('management_key', 'TEXT')],
                'guardian_invites': [('approval_attempts', 'INTEGER NOT NULL DEFAULT 0')],
                'guardian_replies': [('reply_markup', 'TEXT'), ('admin_reply', 'INTEGER NOT NULL DEFAULT 0')],
            }
            for table, columns in additions.items():
                existing = {r['name'] for r in c.execute(f'PRAGMA table_info({table})')}
                for name, declaration in columns:
                    if name not in existing:
                        c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}')
            c.execute('''UPDATE notification_recipients
                SET management_key=lower(hex(randomblob(16))) WHERE management_key IS NULL''')
            statement = ''
            for line in (SCHEMA + HANDOVER_SCHEMA).splitlines(keepends=True):
                statement += line
                if sqlite3.complete_statement(statement):
                    c.execute(statement)
                    statement = ''
            if statement.strip():
                raise RuntimeError('Incomplete operator migration')

    @staticmethod
    def _operator(c):
        return c.execute('SELECT * FROM operator_config WHERE singleton=1').fetchone()

    @staticmethod
    def _button(c, label, kind, target, now, expires=None):
        nonce = secrets.token_urlsafe(18)
        c.execute('INSERT INTO operator_actions VALUES(?,?,?,?)',
                  (nonce, kind, str(target), min(now + 600, expires) if expires else now + 600))
        return {'text': label, 'callback_data': 'o:' + nonce}

    def _notice(self, c, text, now, *, rows=None, invite_id=None, expires=None, key=None):
        operator = self._operator(c)
        if not operator:
            return
        key = key or 'operator:' + secrets.token_hex(16)
        self._reply(c, key, operator['chat_id'], text, now, invite_id, expires or now + TTL)
        c.execute('UPDATE guardian_replies SET admin_reply=1,reply_markup=? WHERE dedupe_key=?',
                  (json.dumps({'inline_keyboard': rows}, ensure_ascii=False) if rows else None, key))

    def _home_button(self, c, now):
        return [self._button(c, '운영자 메뉴', 'home', '', now)]

    def _menu(self, c, now, text='[CareCall 운영자]\n사용할 메뉴를 눌러주세요.'):
        rows = [[self._button(c, label, kind, '', now)] for label, kind in (
            ('보호자 초대', 'invite'), ('등록 대기', 'pending'), ('등록된 보호자', 'recipients'),
            ('운영자 교체', 'handover'))]
        self._notice(c, text, now, rows=rows)

    def begin_bootstrap(self, now=None):
        bot_link = f"https://t.me/{get_bot_username()}?start="
        now = time.time() if now is None else now
        with delivery_gate(self.path), self.transaction() as c:
            if self._operator(c):
                raise ValueError('Operator already configured; use local reset only for deliberate replacement')
            c.execute("DELETE FROM guardian_replies WHERE dedupe_key LIKE 'bootstrap:%'")
            c.execute('DELETE FROM operator_bootstrap')
            token = secrets.token_urlsafe(32)
            c.execute('INSERT INTO operator_bootstrap(singleton,token_hash,expires_at) VALUES(1,?,?)',
                      (hashlib.sha256(token.encode()).hexdigest(), now + TTL))
        return bot_link + 'operator_' + token

    def confirm_bootstrap(self, code, now=None):
        now = time.time() if now is None else now
        accepted = False
        with delivery_gate(self.path), self.transaction() as c:
            row = c.execute('SELECT * FROM operator_bootstrap WHERE singleton=1').fetchone()
            if (not self._operator(c) and row and row['expires_at'] > now
                    and row['chat_id'] and row['attempts'] < 5 and row['code_hash']):
                digest = hashlib.sha256((row['salt'] + code).encode()).hexdigest()
                accepted = bool(re.fullmatch('[0-9]{6}', code)
                                and hmac.compare_digest(row['code_hash'], digest))
                if accepted:
                    c.execute('INSERT INTO operator_config VALUES(1,?,?,?)',
                              (row['chat_id'], secrets.token_hex(16), now))
                    c.execute('DELETE FROM operator_bootstrap')
                    c.execute("DELETE FROM guardian_replies WHERE dedupe_key LIKE 'bootstrap:%'")
                    self._menu(c, now, '운영자 등록이 완료되었습니다.\n이 계정에서 보호자를 관리할 수 있습니다.')
                else:
                    c.execute('UPDATE operator_bootstrap SET attempts=attempts+1 WHERE singleton=1')
        if not accepted:
            raise ValueError('Confirmation failed or expired; no operator permission was granted')

    def reset_operator(self):
        with delivery_gate(self.path), self.transaction() as c:
            self._clear_handover(c)
            c.execute('DELETE FROM operator_config')
            c.execute('DELETE FROM operator_actions')
            c.execute('DELETE FROM operator_state')
            c.execute('DELETE FROM operator_bootstrap')
            c.execute("DELETE FROM guardian_replies WHERE admin_reply=1 OR dedupe_key LIKE 'bootstrap:%'")

    def _bootstrap_message(self, c, message, actor, now):
        match = re.fullmatch(r'/start operator_([A-Za-z0-9_-]{43})', message['text'].strip())
        if not match:
            return False
        row = c.execute('SELECT * FROM operator_bootstrap WHERE singleton=1').fetchone()
        if (self._operator(c) or not row or row['expires_at'] <= now or row['attempts'] >= 5
                or row['chat_id'] not in (None, actor)
                or not hmac.compare_digest(row['token_hash'], hashlib.sha256(match[1].encode()).hexdigest())):
            return True
        if row['chat_id'] is None:
            code, salt = f'{secrets.randbelow(1000000):06d}', secrets.token_hex(16)
            c.execute('UPDATE operator_bootstrap SET chat_id=?,salt=?,code_hash=? WHERE singleton=1',
                      (actor, salt, hashlib.sha256((salt + code).encode()).hexdigest()))
            self._reply(c, 'bootstrap:' + row['token_hash'], actor,
                        '운영자 등록 확인 숫자: ' + code + '\n본인의 Raspberry Pi 등록 화면에 이 숫자를 입력하세요.\n'
                        '등록을 신청하지 않았다면 숫자를 전달하지 마세요.', now, expiry=row['expires_at'])
        return True

    def _pending_notices(self, c, now):
        operator = self._operator(c)
        if not operator:
            return
        for invite in c.execute("SELECT * FROM guardian_invites WHERE state='pending' AND expires_at>?", (now,)).fetchall():
            key = f"operator-pending:{operator['generation']}:{invite['id']}"
            if c.execute('SELECT 1 FROM guardian_replies WHERE dedupe_key=?', (key,)).fetchone():
                continue
            rows = [[self._button(c, '확인 숫자 입력 후 승인', 'approve', invite['id'], now, invite['expires_at'])],
                    self._home_button(c, now)]
            self._notice(c, f"[보호자 등록 대기]\n별칭: {invite['label']}\n세트: {invite['set_id']}\n"
                         '해당 보호자에게 화면의 확인 숫자 6자리를 직접 확인해주세요.', now,
                         rows=rows, invite_id=invite['id'], expires=invite['expires_at'], key=key)

    def _list(self, c, kind, page, now):
        page = max(0, min(page, 10000))
        if kind == 'pending':
            all_rows = c.execute("SELECT * FROM guardian_invites WHERE state='pending' AND expires_at>? ORDER BY id", (now,)).fetchall()
            title = '[등록 대기]'
        else:
            all_rows = c.execute('''SELECT r.*,
                COALESCE((SELECT label FROM guardian_invites g WHERE g.chat_id=r.chat_id
                AND g.set_id=r.set_id AND g.state='approved' ORDER BY id DESC LIMIT 1),
                '기존 수신자') AS label FROM notification_recipients r ORDER BY r.rowid''').fetchall()
            title = '[등록된 보호자]'
        last_page = max(0, (len(all_rows) - 1) // PAGE_SIZE)
        page = min(page, last_page)
        shown = all_rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        buttons = []
        for row in shown:
            label = f"{row['label']} · {row['set_id']}"
            if kind == 'pending':
                buttons.append([self._button(c, label, 'approve', row['id'], now, row['expires_at'])])
            else:
                label += ' · 수신 중' if row['enabled'] else ' · 수신 중지'
                buttons.append([self._button(c, label, 'remove_prompt', row['management_key'], now)])
        nav = []
        if page > 0:
            nav.append(self._button(c, '이전', kind, page - 1, now))
        if page < last_page:
            nav.append(self._button(c, '다음', kind, page + 1, now))
        if nav:
            buttons.append(nav)
        buttons.append(self._home_button(c, now))
        self._notice(c, f'{title}\n총 {len(all_rows)}건' +
                     (f' · {page + 1}/{last_page + 1}쪽\n확인할 항목을 선택해주세요.' if shown else '\n표시할 항목이 없습니다.'),
                     now, rows=buttons)

    @staticmethod
    def _set_state(c, kind, target, now, expires=None):
        c.execute('INSERT OR REPLACE INTO operator_state VALUES(1,?,?,?,?)',
                  (kind, str(target), now, min(now + 600, expires) if expires else now + 600))

    def _callback(self, c, query, now):
        data = query.get('data')
        if not isinstance(data, str) or not re.fullmatch(r'o:[A-Za-z0-9_-]{24}', data):
            return
        action = c.execute('SELECT * FROM operator_actions WHERE nonce=?', (data[2:],)).fetchone()
        if not action or action['expires_at'] <= now:
            self._notice(c, '이미 사용했거나 만료된 버튼입니다. /admin 으로 메뉴를 다시 열어주세요.', now)
            return
        c.execute('DELETE FROM operator_actions WHERE nonce=?', (data[2:],))
        c.execute("DELETE FROM operator_actions WHERE kind='remove_confirm'")
        c.execute('DELETE FROM operator_state')
        kind, target = action['kind'], action['target']
        if kind.startswith('handover'):
            return self._handover_callback(c, kind, target, now)
        c.execute("DELETE FROM operator_actions WHERE kind='handover_confirm'")
        if kind == 'home':
            self._menu(c, now)
        elif kind in ('pending', 'recipients'):
            self._list(c, kind, int(target or '0'), now)
        elif kind == 'invite':
            count = c.execute("SELECT count(*) FROM guardian_invites WHERE state IN ('issued','pending') AND expires_at>?", (now,)).fetchone()[0]
            if count >= 20:
                self._menu(c, now, '미완료 초대가 20건입니다. 등록 대기를 확인하거나 기존 링크가 만료된 뒤 다시 시도해주세요.')
            else:
                self._set_state(c, 'alias', '', now)
                self._notice(c, f'새 보호자의 별칭을 입력해주세요. (1~40자)\n연결 세트: {default_set(c)}\n취소하려면 /cancel 을 보내세요.',
                             now, rows=[self._home_button(c, now)])
        elif kind == 'approve':
            row = c.execute("SELECT * FROM guardian_invites WHERE id=? AND state='pending' AND expires_at>?", (target, now)).fetchone()
            if not row or row['approval_attempts'] >= 5:
                self._menu(c, now, '이미 처리했거나 만료된 등록 요청입니다.')
            else:
                self._set_state(c, 'code', target, now, row['expires_at'])
                self._notice(c, f"별칭: {row['label']}\n세트: {row['set_id']}\n"
                             '해당 보호자의 화면에서 확인한 숫자 6자리를 입력해주세요.\n맞으면 즉시 승인됩니다. 취소: /cancel',
                             now, invite_id=row['id'], expires=row['expires_at'], rows=[self._home_button(c, now)])
        elif kind == 'remove_prompt':
            row = c.execute('''SELECT r.*, COALESCE((SELECT label FROM guardian_invites
                WHERE chat_id=r.chat_id AND state='approved' ORDER BY id DESC LIMIT 1),'기존 수신자') AS label
                FROM notification_recipients r WHERE management_key=?''', (target,)).fetchone()
            if not row:
                self._menu(c, now, '이미 삭제된 보호자입니다.')
                return
            devices = [r[0] for r in c.execute('SELECT set_id FROM notification_recipients WHERE chat_id=? ORDER BY set_id', (row['chat_id'],))]
            operator_note = ('\n이 계정은 운영자입니다. 보호자 수신만 해제하며 운영자 권한과 운영자 ID는 유지합니다.'
                             if row['chat_id'] == self._operator(c)['chat_id'] else '')
            remaining = c.execute('SELECT count(*) FROM notification_recipients WHERE enabled=1 AND chat_id!=?', (row['chat_id'],)).fetchone()[0]
            if remaining == 0:
                operator_note += '\n해제하면 호출 알림을 받는 보호자가 0명이 됩니다.'
            self._notice(c, f"[수신 해제 확인]\n별칭: {row['label']}\n연결 세트: {', '.join(devices)}\n"
                         '이 보호자 계정의 모든 수신 등록과 서버의 보호자 정보를 삭제합니다.\n'
                         '이미 도착한 메시지는 남아 있으며 재등록하려면 새 초대가 필요합니다.' + operator_note,
                         now, rows=[[self._button(c, '수신 해제 및 보호자 정보 삭제', 'remove_confirm', target, now)],
                                    self._home_button(c, now)])
        elif kind == 'remove_confirm':
            removed = self._remove(c, target)
            self._menu(c, now, '수신 등록과 현재 서버 DB의 보호자 정보를 삭제했습니다.\n이전에 도착한 메시지는 자동 삭제되지 않습니다.'
                       if removed else '이미 삭제된 보호자입니다.')

    def _operator_message(self, c, message, now):
        text = message['text'].strip()
        if text == '/cancel':
            self._clear_handover(c)
        if text in ('/admin', '/start', '/cancel', '/help', '/settings'):
            c.execute("DELETE FROM operator_actions WHERE kind='handover_confirm'")
            c.execute('DELETE FROM operator_state')
            c.execute("DELETE FROM operator_actions WHERE kind='remove_confirm'")
            self._menu(c, now)
            return
        state = c.execute('SELECT * FROM operator_state WHERE singleton=1').fetchone()
        if not state or state['expires_at'] <= now or message['date'] < int(state['created_at']):
            self._notice(c, '입력 대기 중인 작업이 없습니다. /admin 으로 메뉴를 열어주세요.', now)
            return
        if state['kind'].startswith('handover_'):
            self._handover_input(c, state, text, now)
            return
        if state['kind'] == 'alias':
            if not text or len(text) > 40 or text.startswith('/') or not all(x.isprintable() for x in text):
                self._notice(c, '별칭은 줄바꿈 없이 1~40자로 입력해주세요. 취소: /cancel', now)
                return
            invite_id, link = self.invite(text, now=now, connection=c)
            c.execute('DELETE FROM operator_state')
            expiry = datetime.fromtimestamp(now + TTL, ZoneInfo('Asia/Seoul'))
            self._notice(c, f'[보호자 초대]\n별칭: {text}\n세트: {default_set(c)}\n유효기간: {expiry:%m/%d %H:%M} KST까지 (15분)\n'
                         f'{link}\n\n이 링크를 해당 보호자에게만 전달해주세요. 보호자가 시작을 누르면 등록 대기가 표시됩니다.',
                         now, invite_id=invite_id, expires=now + TTL, rows=[self._home_button(c, now)])
        elif state['kind'] == 'code':
            row = c.execute('SELECT * FROM guardian_invites WHERE id=?', (state['target'],)).fetchone()
            if not row or row['state'] != 'pending' or row['expires_at'] <= now or row['approval_attempts'] >= 5:
                c.execute('DELETE FROM operator_state')
                self._menu(c, now, '이미 처리했거나 만료된 등록 요청입니다.')
                return
            if not re.fullmatch('[0-9]{6}', text) or not hmac.compare_digest(row['check_code'] or '', text):
                attempts = row['approval_attempts'] + 1
                c.execute('UPDATE guardian_invites SET approval_attempts=? WHERE id=?', (attempts, row['id']))
                if attempts >= 5:
                    c.execute("UPDATE guardian_invites SET state='revoked',check_code=NULL WHERE id=?", (row['id'],))
                    c.execute("UPDATE guardian_replies SET status='cancelled' WHERE invite_id=? AND status='pending'", (row['id'],))
                    c.execute('DELETE FROM operator_state')
                    self._menu(c, now, '확인 숫자가 5회 일치하지 않아 초대를 취소했습니다. 본인 확인 후 새로 초대해주세요.')
                else:
                    self._notice(c, f'확인 숫자가 일치하지 않습니다. ({attempts}/5회)\n보호자와 다시 확인해주세요. 취소: /cancel', now)
                return
            self.approve(row['id'], text, now=now, connection=c)
            c.execute('UPDATE guardian_invites SET check_code=NULL WHERE id=?', (row['id'],))
            c.execute("DELETE FROM guardian_replies WHERE invite_id=? AND dedupe_key NOT LIKE 'approved:%'", (row['id'],))
            c.execute('DELETE FROM operator_state')
            self._menu(c, now, f"등록을 승인했습니다.\n별칭: {row['label']}\n세트: {row['set_id']}")

    def apply_update(self, update, now=None, *, connection=None):
        if connection is not None:
            raise ValueError('Operator updates own their transaction and delivery gate')
        now = time.time() if now is None else now
        update_id = update.get('update_id') if isinstance(update, dict) else None
        if type(update_id) is not int or update_id < 0:
            raise ValueError('invalid_update_id')
        with delivery_gate(self.path), self.transaction() as c:
            last = c.execute('SELECT last_update_id FROM guardian_cursor WHERE singleton=1').fetchone()[0]
            if update_id <= last:
                return 'replayed'
            self._expire_handover(c, now)
            operator = self._operator(c)
            message, query = update.get('message'), update.get('callback_query')
            outcome = 'ignored'
            if isinstance(query, dict) and isinstance(query.get('message'), dict):
                envelope = dict(query['message'])
                envelope['from'] = query.get('from')
                actor = private_actor(envelope)
                if isinstance(query.get('data'), str) and query['data'].startswith('c:'):
                    outcome = apply_call_callback(c, query, actor)
                elif operator and actor == operator['chat_id']:
                    outcome = self._callback(c, query, now) or 'operator'
            elif private_actor(message) and isinstance(message.get('text'), str) and len(message['text']) <= 150:
                actor = private_actor(message)
                fresh = fresh_message(message, now)
                if fresh and self._handover_message(c, message, actor, now):
                    outcome = 'handover_request'
                elif fresh and self._bootstrap_message(c, message, actor, now):
                    outcome = 'bootstrap'
                elif fresh and re.fullmatch(r'/start(?:@' + re.escape(get_bot_username()) + r')?\s+join_[A-Za-z0-9_-]{43}', message['text'].strip()):
                    outcome = super().apply_update(update, now=now, connection=c)
                elif operator and actor == operator['chat_id']:
                    if fresh:
                        self._operator_message(c, message, now)
                        outcome = 'operator'
                elif fresh:
                    if message['text'].strip() in ('/help', '/settings'):
                        self._reply(c, f'help:{update_id}', actor,
                                    'CareCall 도움 요청 알림 봇입니다.\n등록·수신 해제는 담당 운영자에게 요청해주세요.', now)
                    else:
                        outcome = super().apply_update(update, now=now, connection=c)
                if outcome == 'pending':
                    self._pending_notices(c, now)
            c.execute('UPDATE guardian_cursor SET last_update_id=?,last_update_at=? WHERE singleton=1', (update_id, now))
            return outcome

    def _remove(self, c, management_key):
        """Caller holds delivery gate and an IMMEDIATE transaction; no network here."""
        row = c.execute('SELECT rowid AS recipient_id,* FROM notification_recipients WHERE management_key=?',
                        (management_key,)).fetchone()
        if not row:
            return False
        chat_id = row['chat_id']
        handover = c.execute('SELECT candidate_chat_id FROM operator_handover WHERE singleton=1').fetchone()
        if handover and handover['candidate_chat_id'] == chat_id:
            self._clear_handover(c)
        c.execute('''DELETE FROM guardian_audit WHERE invite_id IN
            (SELECT id FROM guardian_invites WHERE chat_id=?) OR recipient_id IN
            (SELECT rowid FROM notification_recipients WHERE chat_id=?)''', (chat_id, chat_id))
        c.execute('''DELETE FROM guardian_replies WHERE chat_id=? OR admin_reply=1 OR invite_id IN
            (SELECT id FROM guardian_invites WHERE chat_id=?)''', (chat_id, chat_id))
        c.execute('DELETE FROM notification_outbox WHERE chat_id=?', (chat_id,))
        c.execute('DELETE FROM guardian_invites WHERE chat_id=?', (chat_id,))
        c.execute('DELETE FROM notification_recipients WHERE chat_id=?', (chat_id,))
        c.execute('DELETE FROM operator_bootstrap WHERE chat_id=?', (chat_id,))
        # Menus can include aliases, and rowids are reusable: invalidate old actions.
        c.execute('DELETE FROM operator_actions')
        c.execute('DELETE FROM operator_state')
        c.execute("INSERT INTO guardian_audit(action,created_at) VALUES('personal_data_removed',?)", (utc_now(),))
        return True

    def housekeeping(self, now=None):
        now = time.time() if now is None else now
        if now < getattr(self, '_next_housekeeping', 0):
            return
        with delivery_gate(self.path), self.transaction() as c:
            self._expire_handover(c, now)
            c.execute('DELETE FROM operator_actions WHERE expires_at<=?', (now,))
            c.execute('DELETE FROM operator_state WHERE expires_at<=?', (now,))
            c.execute('DELETE FROM operator_bootstrap WHERE expires_at<=? OR attempts>=5', (now,))
            c.execute('DELETE FROM guardian_replies WHERE expires_at<=?', (now,))
            expired = "SELECT id FROM guardian_invites WHERE state!='approved' AND (expires_at<=? OR state='revoked')"
            c.execute('DELETE FROM guardian_replies WHERE invite_id IN (' + expired + ')', (now,))
            c.execute('DELETE FROM guardian_audit WHERE invite_id IN (' + expired + ')', (now,))
            c.execute("DELETE FROM guardian_invites WHERE state!='approved' AND (expires_at<=? OR state='revoked')", (now,))
        # Best effort: active readers/writers can retain WAL frames. Not a media wipe.
        with closing(self.connect()) as c:
            c.execute('PRAGMA wal_checkpoint(PASSIVE)')
        self._next_housekeeping = now + 60
