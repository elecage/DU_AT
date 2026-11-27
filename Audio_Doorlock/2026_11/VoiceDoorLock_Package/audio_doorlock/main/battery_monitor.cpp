#include "battery_monitor.h"

#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_rom_sys.h"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>

namespace {
constexpr char TAG[] = "battery_monitor";

// -----------------------------------------------------------------------------
// Hardware configuration
// -----------------------------------------------------------------------------
// Battery pack:
//   3.7 V Li-Po x 2 in parallel -> 1S2P
//
// Divider:
//   PACK+ ---- 100 kOhm ----+---- GPIO3 (ADC)
//                            |
//                          100 kOhm
//                            |
//   PACK- / GND -------------+---- ESP32 GND
//
// A 100 kOhm / 100 kOhm divider divides the pack voltage by 2.
//
// Therefore:
//   PACK 4.20 V -> GPIO3 about 2.10 V  (FULL)
//   PACK 3.30 V -> GPIO3 about 1.65 V  (EMPTY / CRITICAL)
//
// IMPORTANT:
//   The 2.10 V value is the MAXIMUM ADC-side voltage at a full 4.20 V pack.
//   It must NOT be used as the "empty" threshold.
//
//   GPIO3 must measure the battery/PACK side BEFORE the LX-LBCE1/LX-LBCB1
//   5 V boost output. The regulated 5 V output cannot be used to infer SOC.
constexpr int kBatteryAdcGpio = 3;

constexpr uint32_t kDividerTopOhms = 100000;
constexpr uint32_t kDividerBottomOhms = 100000;

// Optional software calibration multiplier.
// 1000 = 1.000x.
// Example: if a multimeter reads 2.050 V at GPIO3 while the firmware reports
// 2.000 V, use about 1025 (= 2.050 / 2.000 * 1000).
constexpr uint32_t kVoltageCalibrationPermille = 1000;

// Informational only. Parallel connection doubles capacity while pack voltage
// remains the same as a single 1S cell.
constexpr uint32_t kNominalPackCapacityMah = 2000;

// -----------------------------------------------------------------------------
// ADC-side voltage limits
// -----------------------------------------------------------------------------
// All SOC/low/critical decisions in this file are intentionally made using
// the ACTUAL VOLTAGE SEEN AT GPIO3, not the reconstructed pack voltage.
//
// With the 1:1 divider:
//   4.20 V pack -> 2.10 V ADC
//   3.50 V pack -> 1.75 V ADC
//   3.30 V pack -> 1.65 V ADC
constexpr uint32_t kAdcFullMv = 2100;
constexpr uint32_t kAdcLowMv = 1750;
constexpr uint32_t kAdcCriticalMv = 1650;
constexpr uint32_t kAdcEmptyMv = 1650;

// Sanity range for the expected 1S Li-Po + 1:1 divider.
// 1.45 V at GPIO3 corresponds to about 2.90 V pack voltage. A real 1S Li-Po
// below this point is already severely over-discharged; in this project, a
// reading far below this value is much more likely to mean an open/incorrect
// divider, wrong measurement node, missing common ground, or other wiring fault.
// 2.20 V corresponds to about 4.40 V pack voltage and catches an incorrect
// divider ratio/over-voltage condition.
constexpr uint32_t kAdcPlausibleMinMv = 1450;
constexpr uint32_t kAdcPlausibleMaxMv = 2200;

// ADC filtering:
// Read several samples, sort them, remove the highest/lowest samples, and
// average the middle values. This reduces Wi-Fi/servo switching noise.
constexpr size_t kRawSampleCount = 24;
constexpr size_t kTrimCountEachSide = 4;
static_assert(kRawSampleCount > (kTrimCountEachSide * 2),
              "Invalid ADC trim settings");
constexpr uint32_t kSampleSpacingUs = 1500;

// Additional low-pass filter between read() calls.
// 1/4 new reading + 3/4 previous reading.
constexpr uint32_t kFilterNewWeight = 1;
constexpr uint32_t kFilterTotalWeight = 4;

// -----------------------------------------------------------------------------
// ADC-input-voltage -> SOC table
// -----------------------------------------------------------------------------
// This is the previous 1S Li-Po voltage/SOC approximation expressed directly
// in the GPIO3 voltage domain.
//
// Example:
//   previous 4.20 V pack point -> 2.10 V GPIO3 point
//   previous 3.30 V pack point -> 1.65 V GPIO3 point
//
// This makes it impossible to accidentally compare a 2.1 V ADC signal against
// a 3.3 V threshold.
//
// Voltage-only SOC remains an estimate. Load current, temperature, cell age,
// servo movement and charge/discharge state can shift the observed voltage.
struct SocPoint {
    uint32_t adc_mv;
    uint8_t percent;
};

constexpr std::array<SocPoint, 21> kSocTable = {{
    {1650,   0},  // pack ~3.30 V
    {1700,   2},  // pack ~3.40 V
    {1750,   5},  // pack ~3.50 V
    {1800,  10},  // pack ~3.60 V
    {1840,  15},  // pack ~3.68 V
    {1850,  20},  // pack ~3.70 V
    {1860,  25},  // pack ~3.72 V
    {1870,  30},  // pack ~3.74 V
    {1880,  35},  // pack ~3.76 V
    {1890,  40},  // pack ~3.78 V
    {1900,  45},  // pack ~3.80 V
    {1910,  50},  // pack ~3.82 V
    {1925,  55},  // pack ~3.85 V
    {1935,  60},  // pack ~3.87 V
    {1955,  65},  // pack ~3.91 V
    {1975,  70},  // pack ~3.95 V
    {1990,  75},  // pack ~3.98 V
    {2010,  80},  // pack ~4.02 V
    {2040,  90},  // pack ~4.08 V
    {2075,  95},  // pack ~4.15 V
    {2100, 100},  // pack ~4.20 V
}};

uint8_t adc_voltage_to_percent(uint32_t adc_mv)
{
    if (adc_mv <= kAdcEmptyMv) {
        return 0;
    }

    if (adc_mv >= kAdcFullMv) {
        return 100;
    }

    // Piecewise-linear interpolation between neighboring SOC points.
    for (size_t i = 1; i < kSocTable.size(); ++i) {
        const SocPoint &low = kSocTable[i - 1];
        const SocPoint &high = kSocTable[i];

        if (adc_mv <= high.adc_mv) {
            const uint32_t span_mv = high.adc_mv - low.adc_mv;
            const uint32_t offset_mv = adc_mv - low.adc_mv;
            const uint32_t span_percent = high.percent - low.percent;

            return static_cast<uint8_t>(
                low.percent +
                ((offset_mv * span_percent) + (span_mv / 2U)) / span_mv);
        }
    }

    return 100;
}

uint32_t adc_input_mv_to_pack_mv(uint32_t adc_mv)
{
    // Undo the 100 kOhm / 100 kOhm divider.
    // 64-bit arithmetic avoids overflow if resistor values are changed later.
    uint64_t pack_mv =
        (static_cast<uint64_t>(adc_mv) *
         (kDividerTopOhms + kDividerBottomOhms)) /
        kDividerBottomOhms;

    return static_cast<uint32_t>(pack_mv);
}

uint32_t apply_voltage_calibration(uint32_t adc_mv)
{
    return static_cast<uint32_t>(
        (static_cast<uint64_t>(adc_mv) * kVoltageCalibrationPermille) / 1000U);
}

} // namespace

esp_err_t BatteryMonitor::init()
{
    if (initialized_) {
        return ESP_OK;
    }

    adc_unit_t unit = ADC_UNIT_1;
    adc_channel_t channel = ADC_CHANNEL_0;

    // Let ESP-IDF resolve GPIO3 to the proper ESP32-C3 ADC unit/channel.
    esp_err_t err =
        adc_oneshot_io_to_channel(kBatteryAdcGpio, &unit, &channel);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "GPIO%d is not ADC-capable: %s",
                 kBatteryAdcGpio, esp_err_to_name(err));
        return err;
    }

    adc_oneshot_unit_handle_t adc_handle = nullptr;
    adc_oneshot_unit_init_cfg_t unit_config = {
        .unit_id = unit,
        .ulp_mode = ADC_ULP_MODE_DISABLE,
    };

    ESP_RETURN_ON_ERROR(
        adc_oneshot_new_unit(&unit_config, &adc_handle),
        TAG,
        "Failed to create ADC unit");

    adc_oneshot_chan_cfg_t channel_config = {
        // ESP32-C3 + 12 dB attenuation has an effective calibrated range up to
        // about 2.5 V. Our divider reaches only about 2.10 V at a full cell,
        // so it stays inside the intended measurement range.
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };

    ESP_RETURN_ON_ERROR(
        adc_oneshot_config_channel(adc_handle, channel, &channel_config),
        TAG,
        "Failed to configure battery ADC channel");

    adc_cali_handle_t cali_handle = nullptr;

#if ADC_CALI_SCHEME_CURVE_FITTING_SUPPORTED
    adc_cali_curve_fitting_config_t cali_config = {
        .unit_id = unit,
        .chan = channel,
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    err = adc_cali_create_scheme_curve_fitting(&cali_config, &cali_handle);
#elif ADC_CALI_SCHEME_LINE_FITTING_SUPPORTED
    adc_cali_line_fitting_config_t cali_config = {
        .unit_id = unit,
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    err = adc_cali_create_scheme_line_fitting(&cali_config, &cali_handle);
#else
    err = ESP_ERR_NOT_SUPPORTED;
#endif

    calibrated_ = (err == ESP_OK);

    if (!calibrated_) {
        // Fallback only. The normal ESP32-C3 path should use the ESP-IDF ADC
        // calibration scheme whenever available.
        ESP_LOGW(TAG,
                 "ADC calibration unavailable; using approximate 0..2500mV conversion: %s",
                 esp_err_to_name(err));
    }

    adc_handle_ = adc_handle;
    cali_handle_ = cali_handle;
    adc_unit_ = static_cast<int>(unit);
    adc_channel_ = static_cast<int>(channel);

    initialized_ = true;
    filter_initialized_ = false;
    filtered_adc_mv_ = 0;

    ESP_LOGI(TAG,
             "Battery monitor ready: gpio=%d divider=%lu:%lu ADC-domain empty=%lumV full=%lumV",
             kBatteryAdcGpio,
             static_cast<unsigned long>(kDividerTopOhms),
             static_cast<unsigned long>(kDividerBottomOhms),
             static_cast<unsigned long>(kAdcEmptyMv),
             static_cast<unsigned long>(kAdcFullMv));

    ESP_LOGI(TAG,
             "Expected divider voltage: PACK 3.30V -> GPIO3 ~1.65V, PACK 4.20V -> GPIO3 ~2.10V");

    ESP_LOGI(TAG,
             "Battery ADC sanity range: GPIO3=%lu..%lumV (pack about %lu..%lumV)",
             static_cast<unsigned long>(kAdcPlausibleMinMv),
             static_cast<unsigned long>(kAdcPlausibleMaxMv),
             static_cast<unsigned long>(adc_input_mv_to_pack_mv(kAdcPlausibleMinMv)),
             static_cast<unsigned long>(adc_input_mv_to_pack_mv(kAdcPlausibleMaxMv)));

    ESP_LOGI(TAG,
             "Battery pack: 1S2P nominal capacity=%lumAh; ADC must be before the 5V boost output",
             static_cast<unsigned long>(kNominalPackCapacityMah));

    return ESP_OK;
}

esp_err_t BatteryMonitor::read(BatterySample &sample)
{
    if (!initialized_) {
        return ESP_ERR_INVALID_STATE;
    }

    auto adc_handle =
        static_cast<adc_oneshot_unit_handle_t>(adc_handle_);
    auto cali_handle =
        static_cast<adc_cali_handle_t>(cali_handle_);
    auto channel =
        static_cast<adc_channel_t>(adc_channel_);

    std::array<uint32_t, kRawSampleCount> adc_mv_samples = {};
    uint32_t raw_sum = 0;

    for (size_t i = 0; i < kRawSampleCount; ++i) {
        int raw = 0;

        ESP_RETURN_ON_ERROR(
            adc_oneshot_read(adc_handle, channel, &raw),
            TAG,
            "Failed to read battery ADC");

        raw_sum += static_cast<uint32_t>(std::max(raw, 0));

        int adc_mv = 0;

        if (calibrated_) {
            ESP_RETURN_ON_ERROR(
                adc_cali_raw_to_voltage(cali_handle, raw, &adc_mv),
                TAG,
                "Failed to calibrate battery ADC");
        } else {
            // ESP32-C3 ATTEN=12 dB effective range is approximately 0..2500 mV.
            // This fallback is less accurate than the calibration driver.
            adc_mv = (raw * 2500) / 4095;
        }

        uint32_t calibrated_adc_mv =
            apply_voltage_calibration(
                static_cast<uint32_t>(std::max(adc_mv, 0)));

        adc_mv_samples[i] = calibrated_adc_mv;

        if (i + 1U < kRawSampleCount) {
            esp_rom_delay_us(kSampleSpacingUs);
        }
    }

    // Trimmed mean filtering.
    std::sort(adc_mv_samples.begin(), adc_mv_samples.end());

    uint64_t trimmed_sum = 0;
    constexpr size_t first = kTrimCountEachSide;
    constexpr size_t last = kRawSampleCount - kTrimCountEachSide;
    constexpr size_t kept_count = last - first;

    for (size_t i = first; i < last; ++i) {
        trimmed_sum += adc_mv_samples[i];
    }

    const uint32_t measured_adc_mv =
        static_cast<uint32_t>(trimmed_sum / kept_count);

    // Slow low-pass filter across repeated battery reads.
    if (!filter_initialized_) {
        filtered_adc_mv_ = measured_adc_mv;
        filter_initialized_ = true;
    } else {
        filtered_adc_mv_ =
            ((filtered_adc_mv_ *
              (kFilterTotalWeight - kFilterNewWeight)) +
             (measured_adc_mv * kFilterNewWeight)) /
            kFilterTotalWeight;
    }

    sample.gpio_mv = filtered_adc_mv_;

    // Matter Power Source BatVoltage expects the BATTERY/PACK voltage, not the
    // divider voltage. Reconstruct the pack voltage only for reporting.
    sample.battery_mv =
        adc_input_mv_to_pack_mv(filtered_adc_mv_);

    // Reject electrically implausible readings instead of converting them to a
    // misleading 0%. The application will publish NULL voltage/percentage to
    // Matter when valid == false and will print an explicit wiring diagnostic.
    sample.valid =
        (filtered_adc_mv_ >= kAdcPlausibleMinMv) &&
        (filtered_adc_mv_ <= kAdcPlausibleMaxMv);

    if (!sample.valid) {
        sample.percent_remaining = 0;
        sample.low = false;
        sample.critical = false;

        ESP_LOGE(
            TAG,
            "Battery measurement INVALID: raw_avg=%lu GPIO3=%lumV filtered_gpio3=%lumV reconstructed_pack=%lumV expected_gpio3=%lu..%lumV. Check PACK+/PACK-, 100k/100k divider, GPIO3 node and common GND.",
            static_cast<unsigned long>(raw_sum / kRawSampleCount),
            static_cast<unsigned long>(measured_adc_mv),
            static_cast<unsigned long>(filtered_adc_mv_),
            static_cast<unsigned long>(sample.battery_mv),
            static_cast<unsigned long>(kAdcPlausibleMinMv),
            static_cast<unsigned long>(kAdcPlausibleMaxMv));
        return ESP_OK;
    }

    // SOC decisions are made directly from the validated GPIO3 voltage.
    sample.percent_remaining =
        adc_voltage_to_percent(filtered_adc_mv_);

    sample.low =
        filtered_adc_mv_ <= kAdcLowMv;

    sample.critical =
        filtered_adc_mv_ <= kAdcCriticalMv;

    ESP_LOGI(
        TAG,
        "Battery: raw_avg=%lu GPIO3=%lumV filtered_gpio3=%lumV pack=%lumV soc=%u%% low=%d critical=%d valid=1",
        static_cast<unsigned long>(raw_sum / kRawSampleCount),
        static_cast<unsigned long>(measured_adc_mv),
        static_cast<unsigned long>(filtered_adc_mv_),
        static_cast<unsigned long>(sample.battery_mv),
        sample.percent_remaining,
        sample.low,
        sample.critical);

    return ESP_OK;
}
