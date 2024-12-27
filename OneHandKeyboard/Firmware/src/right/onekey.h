#pragma once
#include "quantum.h"

#define LAYOUT_onekey( \
    R05, R04, R03, R02, R01, \
    R16, R15, R14, R13, R12, R11, R10, \
    R26, R25, R24, R23, R22, R21, R20, \
    R36, R35, R34, R33, R32, R31, R30, \
    R46, R45, R44 \
) \
    LAYOUT( \
    R03, R04, R02, R05, R01, \
    R16, R13, R10, R14, R12, R15, R11, \
    R26, R23, R20, R24, R22, R25, R21, \
    R36, R33, R30, R34, R32, R35, R31, \
    R44, R46, R45 \
    )
