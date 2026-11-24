#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MT7922_CLC_POWER_INDEX 0U
#define MT7922_CLC_ENTRY_BYTES 16U
#define MT7922_CLC_RULE_HEADER_BYTES 6U
#define MT7922_CLC_REQUEST_BYTES 76U
#define MT7922_CLC_RULE_MAX 768U

typedef struct {
    const uint8_t *Alpha2;
    const uint8_t *Type;
    const uint8_t *Data;
    uint16_t DataBytes;
} MT7922_CLC_RULE;

/* Walk the firmware CLC region like mt7921_load_clc: keep the POWER entry whose
   type bit 0 matches the eFuse HW type, then pick the country rule within it.
   When the country has no rule the "00" rule is used, as in mt7921_mcu_set_clc. */
bool Mt7922ClcSelectPower(const uint8_t *Region, size_t Bytes, uint8_t Encapsulation,
    const uint8_t Country[2], MT7922_CLC_RULE *Rule);

/* MCU_CE_CMD(SET_CLC) request header plus the rule payload, indoor environment,
   no CLC event capability. Returns the total command bytes, zero on bad input. */
size_t Mt7922BuildStationClc(uint8_t *Command, size_t Capacity, const MT7922_CLC_RULE *Rule);
