"""Read Qualcomm HTC service assignments from a Windows WlanLogger ETL."""

import argparse
from collections import Counter
from pathlib import Path
from uuid import UUID
import re

from dissect.etl import ETL


WLAN_LOGGER = UUID("bb6f5b93-635c-47be-816f-e895e77064a8")
ASSIGNMENT = re.compile(
    rb"HTCConnectService,\s*service 0x([0-9a-f]+) connect response from target "
    rb"status:\s*(-?\d+),\s*assigned ep:\s*(-?\d+)", re.IGNORECASE
)
WMI_SERVICES = {0x100: "WMI control", 0x105: "WMI MAC1", 0x106: "WMI MAC2"}


def inspect(path):
    assignments = Counter()
    records = starts = 0
    with path.open("rb") as stream:
        for record in ETL(stream):
            header = record.header
            # dissect.etl can expose the GUID using the byte order of the ETW header.
            if header.provider_id not in (WLAN_LOGGER, UUID(bytes=WLAN_LOGGER.bytes_le)):
                continue
            records += 1
            data = bytes(header.payload)
            if b"H\x00T\x00C\x00" in data:
                data = data.replace(b"\x00", b"")
            starts += b"+HTCConnectService" in data
            for match in ASSIGNMENT.finditer(data):
                service, status, endpoint = match.groups()
                assignments[int(service, 16), int(status), int(endpoint)] += 1
    print(f"{path.name}: WlanLogger records={records}, connect starts={starts}, "
          f"response messages={sum(assignments.values())}")
    for (service, status, endpoint), count in sorted(assignments.items()):
        label = WMI_SERVICES.get(service, "other service")
        print(f"  service=0x{service:04x} ({label}) status={status} endpoint={endpoint} count={count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("etl", nargs="+", type=Path)
    for path in parser.parse_args().etl:
        inspect(path)
