#include "call_outbox.h"

#include <cinttypes>
#include <cstdio>
#include <cstring>
#include <limits>

namespace {
constexpr char NAMESPACE[] = "carecall_calls";
constexpr char KEY[] = "outbox_v1";

bool valid_device_id(const char* value)
{
    if (value == nullptr || value[0] == '\0' || std::strlen(value) > 32) {
        return false;
    }
    for (const char* p = value; *p; ++p) {
        if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') ||
              (*p >= '0' && *p <= '9') || *p == '-' || *p == '_')) {
            return false;
        }
    }
    return true;
}
}

CallOutbox::~CallOutbox()
{
    if (handle_ != 0) {
        nvs_close(handle_);
    }
}

esp_err_t CallOutbox::persist(const Snapshot& next)
{
    // One blob keeps the queue, latest call and boot counter together.
    esp_err_t result = nvs_set_blob(handle_, KEY, &next, sizeof(next));
    if (result == ESP_OK) {
        result = nvs_commit(handle_);
    }
    if (result == ESP_OK) {
        state_ = next;
    } else {
        // A failed flash operation may have taken effect. Do not issue further
        // writes using a stale RAM snapshot; recover the persisted state at boot.
        ready_ = false;
    }
    return result;
}

esp_err_t CallOutbox::open(const char* device_id, std::uint64_t initial_boot_id)
{
    if (handle_ != 0 || !valid_device_id(device_id)) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t result = nvs_open(NAMESPACE, NVS_READWRITE, &handle_);
    if (result != ESP_OK) {
        return result;
    }
    Snapshot next{};
    std::size_t length = sizeof(next);
    result = nvs_get_blob(handle_, KEY, &next, &length);
    if (result == ESP_ERR_NVS_NOT_FOUND) {
        next.version = 1;
        std::strcpy(next.device_id, device_id);
        next.boot_id = (initial_boot_id == 0 ||
                        initial_boot_id == std::numeric_limits<std::uint64_t>::max())
                           ? 1 : initial_boot_id;
    } else if (result == ESP_OK) {
        if (length != sizeof(next) || next.version != 1 ||
            next.count > CAPACITY || next.device_id[32] != '\0' ||
            std::strcmp(next.device_id, device_id) != 0 || next.boot_id == 0 ||
            next.boot_id == std::numeric_limits<std::uint64_t>::max()) {
            return ESP_ERR_INVALID_STATE;
        }
        for (std::size_t i = 0; i < next.count; ++i) {
            if (next.calls[i].boot_id == 0 || next.calls[i].sequence == 0) {
                return ESP_ERR_INVALID_STATE;
            }
        }
        if (next.latest.reserved == 0) {
            // Existing delivered calls predate guardian confirmation support.
            // Undelivered calls remain queued and need confirmation when sent.
            next.latest.reserved = next.count > 0 ? 1 : 2;
        } else if (next.latest.reserved > 2) {
            return ESP_ERR_INVALID_STATE;
        }
        // Persistently advance even when every old call was already delivered.
        // IDs cannot collide across reboot when Wi-Fi/RF has not started yet.
        ++next.boot_id;
    } else {
        return result;
    }
    result = persist(next);
    ready_ = result == ESP_OK;
    return result;
}

esp_err_t CallOutbox::append(std::uint64_t uptime_ms, StoredCall* accepted)
{
    if (!ready_ || accepted == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    if (state_.count == CAPACITY) {
        return ESP_ERR_NO_MEM;
    }
    if (sequence_ == std::numeric_limits<std::uint32_t>::max()) {
        return ESP_ERR_INVALID_STATE;
    }
    Snapshot next = state_;
    const StoredCall call{state_.boot_id, uptime_ms, sequence_ + 1, 0};
    next.calls[next.count++] = call;
    next.latest = call;
    // Use the existing latest-record reserved word: 0=legacy, 1=waiting, 2=confirmed.
    // Blob version/size remain readable by the previous offline-call firmware.
    next.latest.reserved = 1;
    const esp_err_t result = persist(next);
    if (result == ESP_OK) {
        sequence_ = call.sequence;
        *accepted = call;
    }
    return result;
}

bool CallOutbox::front(StoredCall* call) const
{
    if (!ready_ || state_.count == 0 || call == nullptr) {
        return false;
    }
    *call = state_.calls[0];
    return true;
}

bool CallOutbox::latest(StoredCall* call) const
{
    if (!ready_ || state_.latest.sequence == 0 || call == nullptr) {
        return false;
    }
    *call = state_.latest;
    return true;
}

void CallOutbox::event_id(const StoredCall& call,
                         char (&output)[EVENT_ID_SIZE]) const
{
    std::snprintf(output, sizeof(output), "%s-%016" PRIx64 "-%08" PRIu32,
                  state_.device_id, call.boot_id, call.sequence);
}

esp_err_t CallOutbox::acknowledge_stored(const char* event_id_value)
{
    if (!ready_ || event_id_value == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    StoredCall call{};
    if (!front(&call)) {
        return ESP_ERR_NOT_FOUND;
    }
    char expected[EVENT_ID_SIZE]{};
    event_id(call, expected);
    if (std::strcmp(expected, event_id_value) != 0) {
        return ESP_ERR_NOT_FOUND;
    }
    Snapshot next = state_;
    --next.count;
    std::memmove(next.calls, next.calls + 1, next.count * sizeof(StoredCall));
    next.calls[next.count] = {};
    // Database ACK must not clear the latest call / future LED waiting state.
    return persist(next);
}

esp_err_t CallOutbox::confirm_latest(const char* event_id_value)
{
    if (!ready_ || event_id_value == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    StoredCall call{};
    if (!latest(&call)) {
        return ESP_ERR_NOT_FOUND;
    }
    char expected[EVENT_ID_SIZE]{};
    event_id(call, expected);
    if (std::strcmp(expected, event_id_value) != 0) {
        return ESP_ERR_NOT_FOUND;
    }
    if (!waiting()) {
        return ESP_OK;
    }
    Snapshot next = state_;
    next.latest.reserved = 2;
    return persist(next);
}
