"""Inspect selected Wi-Fi IORegistry entries over paired USB for channel hints."""

import asyncio
import re

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.diagnostics import DiagnosticsService


def channel_fields(node, path=()):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from channel_fields(value, (*path, str(key)))
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from channel_fields(value, path)
    elif path and re.search(r"chan(?:nel)?|freq", "/".join(path), re.I):
        if type(node) is int or (isinstance(node, str) and re.fullmatch(r"[0-9]{1,5}", node)):
            if 1 <= int(node) <= 7200:
                yield path, int(node)


async def inspect():
    async with await create_using_usbmux(connection_type="USB") as phone:
        async with DiagnosticsService(phone) as service:
            for source in ("awdl0", "AppleBCMWLANProximityInterface", "wifi"):
                entry = await (service.get_wifi() if source == "wifi"
                               else service.ioregistry(name=source))
                if isinstance(entry, dict) and entry.get("IO80211VirtualInterfaceRole") == "AirLink":
                    print(f"{source}: AirLink role confirmed")
                fields = sorted(set(channel_fields(entry)))
                print(f"{source}: entry present={bool(entry)}, numeric hints={len(fields)}")
                for path, value in fields:
                    # Suppress arbitrary dictionary keys such as peer addresses.
                    labels = [key for key in path if re.fullmatch(r"[A-Za-z][A-Za-z0-9 _-]{0,63}", key)
                              and re.search(r"awdl|chan|freq", key, re.I)]
                    if labels:
                        print(f"  {' / '.join(labels)} = {value}")


if __name__ == "__main__":
    asyncio.run(inspect())
