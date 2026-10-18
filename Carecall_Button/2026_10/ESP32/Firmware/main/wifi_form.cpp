#include "wifi_form.h"
#include <cstring>
#include <cstdint>

void carecall_wifi_clear(void* pointer, std::size_t length)
{
    auto* p = static_cast<volatile unsigned char*>(pointer);
    while (length--) *p++ = 0;
}

namespace {
bool valid_utf8(const unsigned char* s, std::size_t n)
{
    for (std::size_t i = 0; i < n;) {
        const unsigned char first = s[i++];
        if (first < 0x80) {
            if (first < 0x20 || first == 0x7f) return false;
            continue;
        }
        unsigned remaining = 0;
        std::uint32_t cp = 0, minimum = 0;
        if (first >= 0xc2 && first <= 0xdf) { remaining = 1; cp = first & 31; minimum = 0x80; }
        else if (first >= 0xe0 && first <= 0xef) { remaining = 2; cp = first & 15; minimum = 0x800; }
        else if (first >= 0xf0 && first <= 0xf4) { remaining = 3; cp = first & 7; minimum = 0x10000; }
        else return false;
        if (i + remaining > n) return false;
        while (remaining--) {
            if ((s[i] & 0xc0) != 0x80) return false;
            cp = (cp << 6) | (s[i++] & 63);
        }
        if (cp < minimum || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) return false;
        if (cp >= 0x80 && cp <= 0x9f) return false;
    }
    return true;
}

int hex(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

bool decode(const char* p, std::size_t length, char* output, std::size_t capacity)
{
    std::size_t written = 0;
    for (std::size_t i = 0; i < length; ++i) {
        unsigned char ch = static_cast<unsigned char>(p[i]);
        if (ch == '+') ch = ' ';
        else if (ch == '%') {
            if (i + 2 >= length || hex(p[i + 1]) < 0 || hex(p[i + 2]) < 0) return false;
            ch = static_cast<unsigned char>((hex(p[i + 1]) << 4) | hex(p[i + 2]));
            i += 2;
        }
        if (ch == 0 || written + 1 >= capacity) return false;
        output[written++] = static_cast<char>(ch);
    }
    output[written] = 0;
    return true;
}
}

bool carecall_wifi_credentials_valid(const CarecallWifiCredentials& value)
{
    if (!std::memchr(value.ssid, 0, sizeof(value.ssid)) ||
        !std::memchr(value.password, 0, sizeof(value.password))) return false;
    const std::size_t ssid_size = std::strlen(value.ssid);
    const std::size_t password_size = std::strlen(value.password);
    if (!ssid_size || ssid_size > 32 || password_size < 8 || password_size > 63) return false;
    if (!valid_utf8(reinterpret_cast<const unsigned char*>(value.ssid), ssid_size)) return false;
    for (std::size_t i = 0; i < password_size; ++i)
        if (static_cast<unsigned char>(value.password[i]) < 0x20 ||
            static_cast<unsigned char>(value.password[i]) > 0x7e) return false;
    return true;
}

bool carecall_wifi_form_decode(const char* input, std::size_t length, CarecallWifiCredentials& result)
{
    carecall_wifi_clear(&result, sizeof(result));
    CarecallWifiCredentials temporary{};
    bool ssid_seen = false, password_seen = false, ok = true;
    if (!input || !length || length > 512) return false;
    for (std::size_t start = 0; start < length && ok;) {
        std::size_t end = start;
        while (end < length && input[end] != '&') ++end;
        std::size_t eq = start;
        while (eq < end && input[eq] != '=') ++eq;
        if (eq == end) { ok = false; break; }
        if (eq - start == 4 && std::memcmp(input + start, "ssid", 4) == 0 && !ssid_seen) {
            ssid_seen = true;
            ok = decode(input + eq + 1, end - eq - 1, temporary.ssid, sizeof(temporary.ssid));
        } else if (eq - start == 8 && std::memcmp(input + start, "password", 8) == 0 && !password_seen) {
            password_seen = true;
            ok = decode(input + eq + 1, end - eq - 1, temporary.password, sizeof(temporary.password));
        } else ok = false;
        if (end + 1 == length) ok = false;
        start = end + 1;
    }
    ok = ok && ssid_seen && password_seen && carecall_wifi_credentials_valid(temporary);
    if (ok) result = temporary;
    carecall_wifi_clear(&temporary, sizeof(temporary));
    return ok;
}
