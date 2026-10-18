#pragma once
#include "esp_err.h"
#include "wifi_form.h"

// Existing public API, used by app_main.cpp, remains unchanged.
esp_err_t wifi_manager_init();
bool wifi_manager_is_connected();

enum class CarecallWifiPhase {
    Starting, ConnectingSaved, Setup, Queued, Testing, TestOk,
    Saving, Saved, Online, SaveUncertain, Error
};

struct CarecallWifiSetupStatus {
    CarecallWifiPhase phase;
    bool ap_active;
    bool station_ready;
    bool profile_saved;
    bool storage_ready;
    unsigned disconnect_reason;
    const char* notice;  // Only static public codes, never credentials.
};

void carecall_wifi_setup_status(CarecallWifiSetupStatus& result);
esp_err_t carecall_wifi_request_test(const CarecallWifiCredentials& candidate);
esp_err_t carecall_wifi_request_save();
esp_err_t carecall_wifi_request_resume();
