// SPDX-License-Identifier: MIT
// Copyright (c) 2026 makxis
/*
 * ВРО-1 / RocketBoard — формирование телеметрии
 */

#include "telemetry.h"
#include "flight_state.h"
#include "recovery.h"
#include "diagnostics.h"
#include "sd_logger.h"
#include "radio.h"

namespace Telemetry {

static uint16_t g_packetId = 0;

void init(void)
{
    g_packetId = 0;
}

uint16_t packetId(void)
{
    return g_packetId;
}

void build(TelemetryRecord &rec, const SensorData &d, uint32_t nowMs)
{
    rec.timestamp_ms   = nowMs;
    rec.flight_time_ms = FlightManager::flightTimeMs(nowMs);
    rec.packet_id      = ++g_packetId;

    rec.flight_state   = FlightManager::state();
    rec.recovery_state = Recovery::state();

    rec.pressure_pa    = d.pressure_pa;
    rec.temperature_c  = d.temperature_c;
    rec.altitude_m     = d.altitude_m;
    rec.max_altitude_m = FlightManager::maxAltitude();

    rec.accel_x = d.accel_x;
    rec.accel_y = d.accel_y;
    rec.accel_z = d.accel_z;
    rec.gyro_x  = d.gyro_x;
    rec.gyro_y  = d.gyro_y;
    rec.gyro_z  = d.gyro_z;

    rec.sd_status    = SdLogger::status();
    rec.radio_status = Radio::status();

    /* Состояние датчиков сводим к одному значению: обязательные по п. 4.1
     * ТЗ — акселерометр и барометр, поэтому отказ любого из них означает
     * отказ подсистемы целиком. */
    if (Diagnostics::isSet(ERROR_IMU) || Diagnostics::isSet(ERROR_BARO))
        rec.sensor_status = SUBSYS_FAILED;
    else
        rec.sensor_status = SUBSYS_OK;

    rec.error_flags = Diagnostics::flags();
}

} /* namespace Telemetry */
