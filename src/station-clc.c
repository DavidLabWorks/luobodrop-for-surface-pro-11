/* SPDX-License-Identifier: BSD-3-Clause-Clear */
/* Firmware CLC selection, following mt7921_load_clc and mt7921_mcu_set_clc. */
#include "station-clc.h"
#include <string.h>

#define CLC_ENV_INDOOR 1U

static uint32_t ReadLe32(const uint8_t *Bytes)
{
    return (uint32_t)Bytes[0] | (uint32_t)Bytes[1] << 8
        | (uint32_t)Bytes[2] << 16 | (uint32_t)Bytes[3] << 24;
}

static uint16_t ReadLe16(const uint8_t *Bytes)
{
    return (uint16_t)((uint16_t)Bytes[0] | (uint16_t)Bytes[1] << 8);
}

/* The rule payload stays opaque: the firmware decrypts and applies it.
   False means the entry is malformed. */
static bool ScanEntry(const uint8_t *Entry, size_t Bytes, const uint8_t Country[2],
    MT7922_CLC_RULE *Rule, bool *Exact)
{
    MT7922_CLC_RULE fallback = {0};
    size_t offset = MT7922_CLC_ENTRY_BYTES;
    for (uint8_t i = 0; i < Entry[6]; ++i) {
        if (Bytes - offset < MT7922_CLC_RULE_HEADER_BYTES) {
            return false;
        }
        const uint8_t *rule = Entry + offset;
        const uint16_t dataBytes = ReadLe16(rule + 4);
        if (dataBytes > Bytes - offset - MT7922_CLC_RULE_HEADER_BYTES) {
            return false;
        }
        const MT7922_CLC_RULE found = {rule, rule + 2, rule + MT7922_CLC_RULE_HEADER_BYTES,
            dataBytes};
        if (memcmp(rule, Country, 2) == 0) {
            *Rule = found;
            *Exact = true;
            return dataBytes != 0;
        }
        if (rule[0] == '0' && rule[1] == '0' && fallback.Data == NULL) {
            fallback = found;
        }
        offset += MT7922_CLC_RULE_HEADER_BYTES + dataBytes;
    }
    if (fallback.Data != NULL && fallback.DataBytes != 0) {
        *Rule = fallback;
        *Exact = false;
    }
    return true;
}

bool Mt7922ClcSelectPower(const uint8_t *Region, size_t Bytes, uint8_t Encapsulation,
    const uint8_t Country[2], MT7922_CLC_RULE *Rule)
{
    if (Region == NULL || Country == NULL || Rule == NULL) {
        return false;
    }
    MT7922_CLC_RULE fallback = {0};
    size_t offset = 0;
    while (Bytes - offset >= MT7922_CLC_ENTRY_BYTES) {
        const uint8_t *entry = Region + offset;
        const uint32_t entryBytes = ReadLe32(entry);
        if (entryBytes < MT7922_CLC_ENTRY_BYTES || entryBytes > Bytes - offset) {
            return false;
        }
        if (entry[4] == MT7922_CLC_POWER_INDEX && (entry[7] & 1U) == (Encapsulation & 1U)) {
            MT7922_CLC_RULE rule = {0};
            bool exact = false;
            if (!ScanEntry(entry, entryBytes, Country, &rule, &exact)) {
                return false;
            }
            if (exact) {
                *Rule = rule;
                return true;
            }
            if (rule.Data != NULL && fallback.Data == NULL) {
                fallback = rule;
            }
        }
        offset += entryBytes;
    }
    if (fallback.Data == NULL) {
        return false;
    }
    *Rule = fallback;
    return true;
}

size_t Mt7922BuildStationClc(uint8_t *Command, size_t Capacity, const MT7922_CLC_RULE *Rule)
{
    if (Command == NULL || Rule == NULL || Rule->Alpha2 == NULL || Rule->Type == NULL ||
        Rule->Data == NULL || Rule->DataBytes == 0 || Rule->DataBytes > MT7922_CLC_RULE_MAX) {
        return 0;
    }
    const size_t bytes = MT7922_CLC_REQUEST_BYTES + Rule->DataBytes;
    if (bytes > UINT16_MAX || Capacity < bytes) {
        return 0;
    }
    memset(Command, 0, bytes);
    Command[0] = 1; /* ver */
    Command[2] = (uint8_t)bytes;
    Command[3] = (uint8_t)(bytes >> 8);
    Command[4] = MT7922_CLC_POWER_INDEX;
    Command[5] = CLC_ENV_INDOOR;
    memcpy(Command + 8, Rule->Alpha2, 2);
    memcpy(Command + 10, Rule->Type, 2);
    memcpy(Command + MT7922_CLC_REQUEST_BYTES, Rule->Data, Rule->DataBytes);
    return bytes;
}
