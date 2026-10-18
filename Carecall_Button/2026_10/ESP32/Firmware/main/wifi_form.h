#pragma once
#include <cstddef>

struct CarecallWifiCredentials {
    char ssid[33]{};
    char password[64]{};
};

bool carecall_wifi_credentials_valid(const CarecallWifiCredentials& value);
bool carecall_wifi_form_decode(const char* input, std::size_t length,
                              CarecallWifiCredentials& result);
void carecall_wifi_clear(void* pointer, std::size_t length);
