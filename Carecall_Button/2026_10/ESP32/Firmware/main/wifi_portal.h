#pragma once
#include "esp_err.h"

constexpr char CARECALL_WIFI_SETUP_IP[] = "192.168.78.1";
constexpr char CARECALL_WIFI_SETUP_HOST[] = "192.168.78.1:8080";
esp_err_t carecall_wifi_portal_start();
void carecall_wifi_portal_stop();
