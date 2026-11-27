#pragma once

#include "esp_err.h"

#include <cstdint>

struct BatterySample {
    // Reconstructed 1S2P battery-pack voltage in millivolts.
    // Example: about 4200 mV when GPIO3 sees about 2100 mV through the
    // 100 kOhm / 100 kOhm divider.
    uint32_t battery_mv;

    // Actual filtered voltage measured at GPIO3 after the divider.
    // This is useful for diagnosing wiring/divider problems.
    uint32_t gpio_mv;

    // Estimated remaining capacity, 0..100% when valid == true.
    uint8_t percent_remaining;

    // true when the measured GPIO3 voltage is inside a physically plausible
    // range for the expected 1S Li-Po pack and 1:1 divider.
    // If false, Matter reports voltage/percentage as NULL instead of falsely
    // reporting a charged pack as 0%.
    bool valid;

    // true when GPIO3 is at/below the configured low-voltage threshold.
    // Meaningful only when valid == true.
    bool low;

    // true when GPIO3 is at/below the configured critical/empty threshold.
    // Meaningful only when valid == true.
    bool critical;
};

class BatteryMonitor {
public:
    // Initializes GPIO3 ADC for the 1S2P Li-Po voltage-divider input.
    //
    // Expected hardware:
    //
    //   PACK+ ---- 100 kOhm ----+---- GPIO3
    //                            |
    //                          100 kOhm
    //                            |
    //   GND ---------------------+
    //
    // Recommended by Espressif for ADC noise reduction:
    //   GPIO3 ---- 100 nF ceramic capacitor ---- GND
    //
    // 4.20 V pack -> about 2.10 V at GPIO3.
    esp_err_t init();

    // Reads the calibrated GPIO3 divider voltage, filters the samples, validates
    // the measurement, estimates SOC in the 0..2.10 V ADC-input domain, and
    // reconstructs the original pack voltage for Matter BatVoltage reporting.
    esp_err_t read(BatterySample &sample);

private:
    bool initialized_ = false;

    void *adc_handle_ = nullptr;
    void *cali_handle_ = nullptr;

    int adc_unit_ = 0;
    int adc_channel_ = 0;

    bool calibrated_ = false;

    // Low-pass filter state stores the ACTUAL GPIO3 divider voltage, not the
    // reconstructed battery-pack voltage.
    bool filter_initialized_ = false;
    uint32_t filtered_adc_mv_ = 0;
};
