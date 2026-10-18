"""Unprivileged AP-only UI; explicit router-trial mode accepts router credentials."""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import threading

import common as c

ROUTER_PAGE = '''<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>CareCall Wi-Fi 설정</title>
<style>body{margin:0;background:#f1f5f8;color:#18313d;font:16px/1.7 system-ui,sans-serif}
main{max-width:540px;margin:24px auto;padding:28px;background:white;border-radius:22px}
.brand{color:#087c74;font-weight:750}h1{font-size:27px;line-height:1.35}.note{padding:16px;background:#edf8f5;border-radius:12px}
label{display:block;margin-top:14px}input[type=text],input[type=password]{box-sizing:border-box;width:100%;font:inherit;padding:12px;border:1px solid #98aaa9;border-radius:9px}
button{width:100%;padding:14px;margin-top:18px;border:0;border-radius:12px;background:#087c74;color:white;font:inherit;font-weight:700}
button:disabled{opacity:.5}.secondary{background:#edf2f5;color:#18313d}small{color:#52666e}
@media(max-width:600px){main{margin:14px;padding:22px}}</style>
<main><div class="brand">CareCall</div><h1>공유기 Wi-Fi 설정</h1>
<p>CareCall이 사용할 공유기의 이름과 비밀번호를 입력하세요.</p>
<p class="note">연결 시험 중에는 이 Wi-Fi가 잠시 사라집니다. 약 1~2분 뒤 <strong>같은 CareCall 설정 Wi-Fi</strong>에 다시 연결하고 이 주소를 열어 결과를 확인하세요.</p>
<p id="status" role="status">상태를 확인하고 있습니다.</p>
<form id="form" autocomplete="off"><label for="ssid">공유기 Wi-Fi 이름</label>
<input id="ssid" type="text" required maxlength="32" spellcheck="false" autocapitalize="none" autocomplete="off">
<label for="password">공유기 Wi-Fi 비밀번호</label>
<input id="password" type="password" required minlength="8" maxlength="64" autocomplete="new-password">
<label><input id="hidden" type="checkbox"> 숨김 Wi-Fi입니다</label>
<small>WPA2-Personal(AES) 공유기용입니다. 비밀번호는 영문·숫자·공백·기호 8~63자 또는 64자리 16진수 키를 지원합니다.</small>
<button id="submit" disabled>입력한 Wi-Fi 연결 확인</button></form>
<button id="finish" class="secondary" disabled>저장된 Wi-Fi로 다시 연결</button>
<p><small>연결 확인 후 이 화면으로 돌아와 저장 버튼을 누르면, 다음 전원 켜기부터도 해당 Wi-Fi를 사용합니다.</small></p>
</main><script>
const $=id=>document.getElementById(id);let token='';
const notices={READY:'공유기 정보를 입력하세요.',CONNECTED:'공유기 연결을 확인했습니다. 아래 저장 버튼을 누르면 설정 Wi-Fi가 종료되고 입력한 공유기로 연결됩니다.',CONNECT_TIMEOUT:'제한 시간 안에 연결을 확인하지 못했습니다. Wi-Fi 이름·비밀번호·신호·보안 방식을 확인하고 다시 입력하세요.',CANDIDATE_SERVICE_STOPPED:'연결 시험 프로세스가 종료됐습니다. 다시 시도하거나 시험을 종료하세요.',ROUTER_SUBNET_CONFLICT:'공유기의 주소 대역이 설정 Wi-Fi와 겹칩니다. 다른 공유기를 사용하거나 저장된 Wi-Fi로 다시 연결하세요.',INVALID_REQUEST:'입력 요청을 처리하지 못했습니다. 다시 입력해 주세요.',CONNECTING:'공유기 연결을 시험하고 있습니다. 같은 설정 Wi-Fi에 다시 연결해 이 페이지를 열어 주세요.'};
notices.CONTROL_STATUS_UNAVAILABLE='기기 내부에서 연결 상태를 조회하지 못했습니다. 저장된 Wi-Fi로 다시 연결하거나 다시 시도하세요.';
notices.SAVE_FAILED='설정을 적용하지 못해 이전 설정을 복구했습니다. 공유기 정보를 확인하고 다시 입력하세요.';
notices.RECOVERED='설정 화면을 다시 열었습니다. 공유기 정보를 입력하거나 저장된 Wi-Fi로 다시 연결하세요.';
notices.SAVED_UNAVAILABLE='저장된 Wi-Fi에 연결하지 못했습니다. 공유기 정보를 다시 입력하세요.';
async function load(){try{const r=await fetch('/session',{cache:'no-store'});if(!r.ok)throw Error();const s=await r.json();if(s.mode!=='router')throw Error();token=s.token;$('status').textContent=notices[s.notice]||'화면을 다시 열어 주세요.';$('submit').disabled=!s.can_submit;$('form').hidden=!s.can_submit;$('finish').textContent=s.can_confirm?'이 Wi-Fi로 저장하고 연결':'저장된 Wi-Fi로 다시 연결';$('finish').disabled=false;}catch(e){$('status').textContent='같은 CareCall 설정 Wi-Fi에 연결한 뒤 페이지를 다시 열어 주세요.';}}
async function post(path,body){return fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CareCall-Token':token},body:JSON.stringify(body)});}
$('form').onsubmit=async e=>{e.preventDefault();if(new TextEncoder().encode($('ssid').value).length>32){$('status').textContent='Wi-Fi 이름은 UTF-8 기준 32바이트 이내여야 합니다.';return;}
$('submit').disabled=true;$('finish').disabled=true;try{const r=await post('/submit',{token,ssid:$('ssid').value,password:$('password').value,hidden:$('hidden').checked});if(!r.ok){$('status').textContent='입력 형식이 맞지 않거나 요청이 진행 중입니다. 이름과 비밀번호의 길이를 확인하세요.';$('submit').disabled=false;$('finish').disabled=false;return;}$('password').value='';$('status').textContent='요청했습니다. 설정 Wi-Fi가 잠시 사라집니다. 약 1~2분 뒤 같은 설정 Wi-Fi에 다시 연결하고 이 페이지를 열어 주세요.';}catch(e){$('password').value='';$('status').textContent='연결이 끊겼습니다. 같은 설정 Wi-Fi에 다시 연결해 결과를 확인하세요.';}};
$('finish').onclick=async()=>{$('finish').disabled=true;$('submit').disabled=true;try{const r=await post('/confirm',{token});if(!r.ok)throw Error();$('status').textContent='공유기로 연결합니다. 설정 Wi-Fi가 종료됩니다. 연결에 실패하면 같은 설정 Wi-Fi가 다시 나타납니다.';}catch(e){$('status').textContent='연결을 전환하고 있습니다. 설정 Wi-Fi가 다시 나타나면 연결해 결과를 확인하세요.';}};load();
</script></html>'''

PAGE = '''<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CareCall 연결 시험</title><style>
body{margin:0;background:#f1f5f8;color:#18313d;font:17px/1.65 system-ui,sans-serif}
main{max-width:520px;margin:40px auto;padding:28px;background:white;border-radius:22px}
.brand{color:#087c74;font-weight:750}h1{font-size:27px;line-height:1.35}
.note{padding:16px;background:#edf8f5;border-radius:12px}button{width:100%;padding:16px;
border:0;border-radius:12px;background:#087c74;color:white;font:inherit;font-weight:700}
button:disabled{opacity:.5}#result{min-height:55px}small{color:#52666e}
@media(max-width:600px){main{margin:18px;padding:24px}}
</style><main><div class="brand">CareCall</div><h1>휴대폰과 장치가<br>연결되었습니다.</h1>
<p>현재 화면은 Wi-Fi 설정을 위한 첫 연결 시험입니다.</p>
<p class="note">아래 버튼을 누르면 장치가 기존 연구실 Wi-Fi로 돌아갑니다.
이 설정용 Wi-Fi는 사라지고 휴대폰 연결도 종료됩니다.</p>
<button id="done" disabled>시험 완료 · 기존 Wi-Fi로 복귀</button><p id="result">연결 상태를 확인하고 있습니다.</p>
<small>이번 시험에서는 공유기 이름이나 비밀번호를 입력하지 않습니다.
아무것도 누르지 않아도 시험 시작 약 3분 후 복귀를 시도합니다.</small></main>
<script>
let token=''; const result=document.getElementById('result'), done=document.getElementById('done');
fetch('/session',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error();return r.json()})
.then(s=>{token=s.token;done.disabled=false;result.textContent='화면이 정상적으로 열렸다면 시험을 완료해 주세요.'})
.catch(()=>{result.textContent='시험 시간이 끝났거나 연결이 종료되었습니다.'});
done.onclick=async()=>{done.disabled=true;try{const r=await fetch('/confirm',{method:'POST',
headers:{'Content-Type':'application/json','X-CareCall-Token':token},body:JSON.stringify({token})});
if(!r.ok)throw Error();result.textContent='확인했습니다. 장치가 기존 Wi-Fi로 돌아갑니다. 개발 PC에서 결과를 확인해 주세요.';
}catch(e){result.textContent='응답을 확인하지 못했습니다. 자동 복귀를 기다린 뒤 개발 PC에서 결과를 확인해 주세요.'}};
</script></html>'''


def valid_confirmation(host, origin, header, body, expected_token, expected_host):
    return (host == expected_host and origin == 'http://' + expected_host
            and isinstance(body, dict) and set(body) == {'token'}
            and isinstance(body['token'], str) and isinstance(header, str)
            and body['token'].isascii() and header.isascii()
            and hmac.compare_digest(body['token'], expected_token)
            and hmac.compare_digest(header, expected_token))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 8

    def __init__(self, address, public):
        self.public = Path(public)
        self.slots = threading.BoundedSemaphore(12)
        super().__init__(address, Handler)
        self.expected_host = f'{address[0]}:{self.server_address[1]}'

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, *_):
        pass  # Never emit request bodies or exception snippets to service logs.


class Handler(BaseHTTPRequestHandler):
    server_version = 'CareCall'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_):
        pass

    def reply(self, status, body, content_type='application/json; charset=utf-8'):
        data = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.headers.get('Host') != self.server.expected_host:
            return self.reply(403, '{}')
        if self.path == '/':
            try:
                session = json.loads((self.server.public / 'session.json').read_text())
                page = ROUTER_PAGE if session.get('mode') == 'router' else PAGE
            except (OSError, ValueError):
                return self.reply(503, '{}')
            return self.reply(200, page, 'text/html; charset=utf-8')
        if self.path == '/session':
            try:
                data = json.loads((self.server.public / 'session.json').read_text())
                return self.reply(200, json.dumps({key: data[key] for key in
                    ('token', 'mode', 'notice', 'attempts', 'can_submit', 'can_confirm', 'seconds_remaining') if key in data}))
            except (OSError, ValueError, KeyError):
                return self.reply(503, '{}')
        self.reply(404, '{}')

    def do_POST(self):
        try:
            if self.path not in ('/confirm', '/submit') or self.headers.get('Transfer-Encoding'):
                return self.reply(400, '{}')
            length = int(self.headers.get('Content-Length', '-1'))
            if not 1 <= length <= (4096 if self.path == '/submit' else 256) or self.headers.get_content_type() != 'application/json':
                return self.reply(400, '{}')
            body = json.loads(self.rfile.read(length))
            session = json.loads((self.server.public / 'session.json').read_text())
            authentication = {'token': body.get('token')} if self.path == '/submit' and isinstance(body, dict) else body
            if not valid_confirmation(self.headers.get('Host'), self.headers.get('Origin'),
                    self.headers.get('X-CareCall-Token'), authentication, session['token'], self.server.expected_host):
                return self.reply(403, '{}')
            if self.path == '/submit':
                if session.get('mode') != 'router' or not session.get('can_submit'):
                    return self.reply(409, '{}')
                import router
                try:
                    router.credentials(body)
                except c.TrialError:
                    return self.reply(400, '{"error":"INVALID_CREDENTIAL_FORMAT"}')
                target = self.server.public / 'request.json'
                # Publish by link only after a complete fsynced file is ready.
                import tempfile
                fd, temporary = tempfile.mkstemp(prefix='.request-', dir=self.server.public)
                try:
                    os.fchmod(fd, 0o600)
                    with os.fdopen(fd, 'w', encoding='utf-8') as f:
                        json.dump(body, f, ensure_ascii=True)
                        f.flush()
                        os.fsync(f.fileno())
                    try:
                        os.link(temporary, target, follow_symlinks=False)
                    except FileExistsError:
                        return self.reply(409, '{}')
                finally:
                    os.unlink(temporary)
                return self.reply(200, '{"accepted":true}')
            target = self.server.public / 'confirmed'
            try:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'w') as f:
                    f.write(session['token'])
                    f.flush()
                    os.fsync(f.fileno())
            except FileExistsError:
                pass
            self.reply(200, '{"accepted":true}')
        except (OSError, ValueError, KeyError):
            self.reply(400, '{}')


if __name__ == '__main__':
    Server((c.AP_IP, c.AP_PORT), c.RUN / 'public').serve_forever()
