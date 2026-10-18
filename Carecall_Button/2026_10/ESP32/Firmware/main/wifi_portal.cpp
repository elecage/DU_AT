#include "wifi_portal.h"
#include "wifi_manager.h"
#include "mqtt_manager.h"
#include <cstdio>
#include <cstdint>
#include <cstring>
#include "esp_http_server.h"
#include "esp_log.h"
#include "sdkconfig.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"

namespace {
httpd_handle_t server = nullptr;
char session_token[33]{};
constexpr char PORTAL_VERSION[] = "20260930-httpaddr-2";
constexpr char PAGE[] = R"CARECALL(<!doctype html>
<html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CareCall 호출 버튼 Wi-Fi 설정</title>
<style>body{font:16px/1.6 system-ui,sans-serif;background:#eef4f5;color:#182f36;margin:0;padding:24px}main{max-width:480px;margin:auto;background:white;padding:26px;border-radius:18px}h1{font-size:25px;margin:4px 0 18px}.brand{color:#087c74;font-weight:700}label{display:block;margin-top:16px}input,button{box-sizing:border-box;width:100%;font:inherit;padding:12px;border-radius:9px;border:1px solid #afc4c8}button{margin-top:16px;background:#087c74;color:white;font-weight:700;border:0}button.secondary{background:#edf4f3;color:#155c55}button:disabled{opacity:.45}.notice{background:#eef8f5;padding:14px;border-radius:10px}.small{font-size:14px;color:#53686e}#error{color:#a62c2c}</style>
<main><div class="brand">CareCall · 호출 버튼</div><h1>공유기 Wi-Fi 설정</h1>
<p class="notice" id="notice">상태를 확인하고 있습니다.</p><p id="link" class="small"></p>
<form id="form"><label for="ssid">공유기 Wi-Fi 이름</label><input id="ssid" name="ssid" maxlength="32" required autocomplete="off">
<label for="password">공유기 Wi-Fi 비밀번호</label><input id="password" name="password" type="password" minlength="8" maxlength="63" required autocomplete="new-password">
<p class="small">2.4GHz WPA2-Personal 또는 WPA2 접속을 허용하는 혼합 네트워크를 사용하세요. 비밀번호는 영문·숫자·기호 8~63자입니다.</p>
<button id="test" disabled>입력한 Wi-Fi 연결 확인</button></form>
<button id="save" hidden>이 Wi-Fi로 저장하고 연결</button>
<button id="resume" class="secondary" disabled>이전 설정으로 다시 연결</button>
<p id="error" role="alert"></p><p class="small">연결 시험 중 설정 Wi-Fi가 잠시 끊길 수 있습니다. 같은 CareCall-ESP32 Wi-Fi에 다시 연결하고 이 주소를 여세요. 연결 확인 후 저장 버튼을 눌러야 영구 저장됩니다.</p></main>
<script>
const $=id=>document.getElementById(id);let token='',busy=false;
const messages={STARTING:'설정 준비 중입니다.',FIRST_SETUP:'공유기 정보를 입력하고 연결을 확인해 주세요.',CONNECTING:'이전 설정으로 연결하고 있습니다.',REQUEST_ACCEPTED:'요청을 받았습니다. 잠시 기다려 주세요.',TESTING:'공유기 연결을 확인 중입니다. 설정 Wi-Fi가 끊기면 다시 연결해 주세요.',TEST_OK:'연결을 확인했습니다. 아래 저장 버튼을 눌러 완료하세요.',SAVING:'설정을 저장하고 있습니다.',SAVED:'저장했습니다. 설정 Wi-Fi가 종료되고 공유기에 연결됩니다.',ONLINE:'저장된 공유기에 연결됐습니다.',TEST_TIMEOUT:'연결을 확인하지 못했습니다. 이름·비밀번호·신호·2.4GHz 지원 여부를 확인하고 다시 입력하세요.',TEST_CONNECTION_LOST:'저장 전에 연결이 끊겼습니다. 다시 연결 확인을 진행하세요.',SUBNET_CONFLICT:'공유기 주소 범위가 설정 Wi-Fi와 겹칩니다. 다른 네트워크가 필요합니다.',SAVED_UNAVAILABLE:'저장된 공유기에 연결할 수 없습니다. 새 공유기 정보를 입력하세요.',RECOVERED:'설정 화면을 다시 열었습니다.',RADIO_ERROR:'무선 장치를 다시 준비하고 있습니다.',STORAGE_ERROR:'저장된 설정을 읽지 못했습니다. 터미널 상태를 확인해 주세요.',SAVE_UNCERTAIN_REBOOT:'저장 결과를 확정하지 못했습니다. 전원을 다시 켠 뒤 상태를 확인해 주세요.'};
async function refresh(){if(busy)return;try{const r=await fetch('/status',{cache:'no-store'});if(!r.ok)throw Error();const s=await r.json();token=s.token;$('notice').textContent=messages[s.notice]||'상태를 확인해 주세요.';$('link').textContent='공유기 연결: '+(s.station_ready?'확인됨':'대기 중')+' · Pi MQTT: '+(s.mqtt_ready?'연결됨':'대기 중');$('test').disabled=!s.can_test;$('save').hidden=!s.can_save;$('resume').disabled=!s.can_resume;if(!s.storage_ready)$('error').textContent='설정 저장 기능을 사용할 수 없습니다. 재부팅 후 로그를 확인해 주세요.';}catch(e){$('notice').textContent='같은 CareCall-ESP32 설정 Wi-Fi에 다시 연결한 뒤 이 페이지를 열어 주세요.';$('test').disabled=true;$('save').hidden=true;$('resume').disabled=true;}}
async function send(path,body){if(busy||!token)return;busy=true;$('error').textContent='';$('test').disabled=true;$('save').hidden=true;$('resume').disabled=true;try{const headers={'X-CareCall-Token':token};if(path==='/test')headers['Content-Type']='application/x-www-form-urlencoded';const r=await fetch(path,{method:'POST',headers,body});if(!r.ok)throw Error();$('notice').textContent='요청을 받았습니다. 설정 Wi-Fi가 끊기면 다시 연결해 주세요.';}catch(e){$('error').textContent='요청 완료를 확인하지 못했습니다. 같은 설정 Wi-Fi에 다시 연결하고 상태를 확인하세요.';}finally{busy=false;}}
$('form').addEventListener('submit',e=>{e.preventDefault();const body=new URLSearchParams({ssid:$('ssid').value,password:$('password').value}).toString();$('password').value='';send('/test',body);});$('save').onclick=()=>send('/save','');$('resume').onclick=()=>send('/resume','');refresh();setInterval(refresh,2000);
</script></html>)CARECALL";

void headers(httpd_req_t* req)
{
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    httpd_resp_set_hdr(req, "X-Content-Type-Options", "nosniff");
    httpd_resp_set_hdr(req, "X-Frame-Options", "DENY");
    httpd_resp_set_hdr(req, "Referrer-Policy", "no-referrer");
    httpd_resp_set_hdr(req, "Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'");
}

esp_err_t error(httpd_req_t* req, const char* status, const char* reason = "REQUEST")
{
    headers(req);
    httpd_resp_set_status(req, status);
    httpd_resp_set_type(req, "text/plain; charset=utf-8");
    httpd_resp_set_hdr(req, "Connection", "close");
    char message[160]{};
    std::snprintf(message, sizeof(message),
        "Request rejected. Reload the setup page. Code=%s Portal=%s", reason, PORTAL_VERSION);
    ESP_LOGW("WIFI_PORTAL", "REQUEST_REJECTED=%s PORTAL_VERSION=%s", reason, PORTAL_VERSION);
    httpd_resp_send(req, message, HTTPD_RESP_USE_STRLEN);
    return ESP_FAIL;  // Close the session, including any unread request body.
}

bool header_equals(httpd_req_t* req, const char* key, const char* expected)
{
    char value[96]{};
    const auto length = httpd_req_get_hdr_value_len(req, key);
    return length == std::strlen(expected) && length < sizeof(value) &&
        httpd_req_get_hdr_value_str(req, key, value, sizeof(value)) == ESP_OK &&
        std::strcmp(value, expected) == 0;
}

const char* setup_rejection(httpd_req_t* req)
{
    // In ESP-IDF 5.4.1 the HTTP listener uses PF_INET6 when IPv6 is enabled.
    // An IPv4 client can therefore have an IPv4-mapped IPv6 local address.
    sockaddr_storage local{};
    socklen_t length = sizeof(local);
    if (getsockname(httpd_req_to_sockfd(req), reinterpret_cast<sockaddr*>(&local), &length) != 0)
        return "LOCAL_SOCKET";
    std::uint32_t ipv4 = 0;
    if (local.ss_family == AF_INET) {
        if (length < sizeof(sockaddr_in)) return "LOCAL_LENGTH";
        ipv4 = reinterpret_cast<const sockaddr_in*>(&local)->sin_addr.s_addr;
    }
#if CONFIG_LWIP_IPV6
    else if (local.ss_family == AF_INET6) {
        if (length < sizeof(sockaddr_in6)) return "LOCAL_LENGTH";
        const auto* addr = reinterpret_cast<const sockaddr_in6*>(&local);
        const auto* bytes = reinterpret_cast<const unsigned char*>(&addr->sin6_addr);
        constexpr unsigned char mapped_prefix[12] = {0,0,0,0,0,0,0,0,0,0,0xff,0xff};
        if (std::memcmp(bytes, mapped_prefix, sizeof(mapped_prefix)) != 0)
            return "LOCAL_NOT_MAPPED_IPV4";
        std::memcpy(&ipv4, bytes + 12, sizeof(ipv4));
    }
#endif
    else return "LOCAL_FAMILY";
    if (ipv4 != inet_addr(CARECALL_WIFI_SETUP_IP)) return "LOCAL_ADDRESS";
    CarecallWifiSetupStatus state{}; carecall_wifi_setup_status(state);
    if (!state.ap_active) return "AP_INACTIVE";
    if (!header_equals(req, "Host", CARECALL_WIFI_SETUP_HOST)) return "HOST_HEADER";
    return nullptr;
}

const char* post_rejection(httpd_req_t* req)
{
    if (const char* reason = setup_rejection(req)) return reason;
    if (!header_equals(req, "Origin", "http://192.168.78.1:8080")) return "ORIGIN_HEADER";
    char supplied[33]{};
    if (httpd_req_get_hdr_value_len(req, "X-CareCall-Token") != 32 ||
        httpd_req_get_hdr_value_str(req, "X-CareCall-Token", supplied, sizeof(supplied)) != ESP_OK)
        return "SESSION_TOKEN";
    unsigned different = 0;
    for (unsigned i = 0; i < 32; ++i) different |= static_cast<unsigned char>(supplied[i] ^ session_token[i]);
    return different == 0 ? nullptr : "SESSION_TOKEN";
}

esp_err_t handle_index(httpd_req_t* req)
{
    if (const char* reason = setup_rejection(req)) return error(req, "403 Forbidden", reason);
    headers(req); httpd_resp_set_type(req, "text/html; charset=utf-8");
    return httpd_resp_send(req, PAGE, sizeof(PAGE) - 1);
}

esp_err_t state(httpd_req_t* req)
{
    if (const char* reason = setup_rejection(req)) return error(req, "403 Forbidden", reason);
    CarecallWifiSetupStatus s{}; carecall_wifi_setup_status(s);
    const bool idle = s.phase == CarecallWifiPhase::Setup || s.phase == CarecallWifiPhase::TestOk;
    const bool can_save = s.phase == CarecallWifiPhase::TestOk && s.station_ready && s.storage_ready;
    char output[512]{};
    const int size = std::snprintf(output, sizeof(output),
        "{\"version\":\"20260930-esp32-wifi-1\",\"portal_version\":\"20260930-httpaddr-2\",\"token\":\"%s\",\"notice\":\"%s\","
        "\"station_ready\":%s,\"mqtt_ready\":%s,\"profile_saved\":%s,\"storage_ready\":%s,"
        "\"can_test\":%s,\"can_save\":%s,\"can_resume\":%s}", session_token, s.notice,
        s.station_ready ? "true" : "false", mqtt_manager_is_ready_to_publish() ? "true" : "false",
        s.profile_saved ? "true" : "false", s.storage_ready ? "true" : "false",
        idle ? "true" : "false", can_save ? "true" : "false", idle ? "true" : "false");
    if (size < 0 || size >= static_cast<int>(sizeof(output))) return error(req, "500 Internal Server Error");
    headers(req); httpd_resp_set_type(req, "application/json");
    return httpd_resp_send(req, output, size);
}

esp_err_t post(httpd_req_t* req)
{
    if (const char* reason = post_rejection(req)) return error(req, "403 Forbidden", reason);
    const bool testing = std::strcmp(req->uri, "/test") == 0;
    esp_err_t result = ESP_ERR_INVALID_ARG;
    if (testing) {
        if (!req->content_len || req->content_len > 512) return error(req, "413 Content Too Large");
        char type[80]{};
        constexpr char expected[] = "application/x-www-form-urlencoded";
        if (httpd_req_get_hdr_value_str(req, "Content-Type", type, sizeof(type)) != ESP_OK ||
            std::strncmp(type, expected, sizeof(expected) - 1) != 0 ||
            (type[sizeof(expected) - 1] != 0 && type[sizeof(expected) - 1] != ';'))
            return error(req, "415 Unsupported Media Type");
        char body[513]{}; std::size_t read = 0;
        const auto started = esp_timer_get_time();
        while (read < req->content_len) {
            const int count = httpd_req_recv(req, body + read, req->content_len - read);
            if (count <= 0 || esp_timer_get_time() - started > 5000000) {
                carecall_wifi_clear(body, sizeof(body)); return error(req, "408 Request Timeout");
            }
            read += count;
        }
        CarecallWifiCredentials credentials{};
        if (carecall_wifi_form_decode(body, read, credentials)) result = carecall_wifi_request_test(credentials);
        carecall_wifi_clear(body, sizeof(body)); carecall_wifi_clear(&credentials, sizeof(credentials));
    } else {
        if (req->content_len != 0) return error(req, "400 Bad Request");
        if (std::strcmp(req->uri, "/save") == 0) result = carecall_wifi_request_save();
        if (std::strcmp(req->uri, "/resume") == 0) result = carecall_wifi_request_resume();
    }
    if (result != ESP_OK) return error(req, result == ESP_ERR_INVALID_ARG ? "400 Bad Request" : "409 Conflict");
    headers(req); httpd_resp_set_status(req, "202 Accepted"); httpd_resp_set_type(req, "application/json");
    return httpd_resp_send(req, "{\"accepted\":true}", HTTPD_RESP_USE_STRLEN);
}
}

esp_err_t carecall_wifi_portal_start()
{
    if (server) return ESP_OK;
    ESP_LOGI("WIFI_PORTAL", "PORTAL_VERSION=%s", PORTAL_VERSION);
    unsigned char random[16]{}; esp_fill_random(random, sizeof(random));
    for (unsigned i = 0; i < 16; ++i) std::snprintf(session_token + i * 2, 3, "%02x", random[i]);
    carecall_wifi_clear(random, sizeof(random));
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = 8080;
    config.stack_size = 6144;
    config.max_open_sockets = 3;
    config.max_uri_handlers = 5;
    config.lru_purge_enable = true;
    config.recv_wait_timeout = 2;
    config.send_wait_timeout = 2;
    esp_err_t result = httpd_start(&server, &config);
    if (result != ESP_OK) { server = nullptr; return result; }
    const char* paths[] = {"/", "/status", "/test", "/save", "/resume"};
    for (unsigned i = 0; i < 5; ++i) {
        httpd_uri_t uri{};
        uri.uri = paths[i]; uri.method = i < 2 ? HTTP_GET : HTTP_POST;
        uri.handler = i == 0 ? handle_index : (i == 1 ? state : post);
        result = httpd_register_uri_handler(server, &uri);
        if (result != ESP_OK) { carecall_wifi_portal_stop(); return result; }
    }
    return ESP_OK;
}

void carecall_wifi_portal_stop()
{
    if (server) { httpd_stop(server); server = nullptr; }
    carecall_wifi_clear(session_token, sizeof(session_token));
}
