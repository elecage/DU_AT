/* SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

// I2C
#define I2C_DRIVER I2CD0
#define I2C1_SDA_PIN GP0
#define I2C1_SCL_PIN GP1

// pull-up resistor
#ifndef I2C1_SCL_PAL_MODE
#define I2C1_SCL_PAL_MODE (PAL_MODE_ALTERNATE_I2C | PAL_RP_PAD_SLEWFAST | PAL_RP_PAD_PUE | PAL_RP_PAD_DRIVE4)
#endif

#ifndef I2C1_SDA_PAL_MODE
#define I2C1_SDA_PAL_MODE (PAL_MODE_ALTERNATE_I2C | PAL_RP_PAD_SLEWFAST | PAL_RP_PAD_PUE | PAL_RP_PAD_DRIVE4)
#endif

// Cirque
#define CIRQUE_PINNACLE_DIAMETER_MM 35
#define CIRQUE_PINNACLE_ADDR 0x2A
#define CIRQUE_PINNACLE_TAP_ENABLE
#define CIRQUE_PINNACLE_SECONDARY_TAP_ENABLE
#define POINTING_DEVICE_GESTURES_SCROLL_ENABLE
#define POINTING_DEVICE_ROTATION_270
#define CIRQUE_PINNACLE_POSITION_MODE 0

// TapDance
#define VIAL_TAP_DANCE_ENTRIES 34
