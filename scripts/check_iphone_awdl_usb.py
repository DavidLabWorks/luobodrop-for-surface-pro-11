"""Count iPhone interface traffic and AirDrop mDNS markers over paired USB."""

import argparse
import asyncio
from collections import Counter

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.pcapd import PcapdService


async def inspect(seconds):
    counts = Counter()
    airdrop = Counter()
    async with await create_using_usbmux(connection_type="USB") as phone:
        async with PcapdService(phone) as capture:
            print("iPhone packet service ready", flush=True)
            try:
                async with asyncio.timeout(seconds):
                    async for packet in capture.watch():
                        name = packet.interface_name
                        if not name[-1:].isdigit():
                            name += str(packet.unit)
                        counts[name] += 1
                        # Two consecutive literal DNS labels; compressed names may be missed.
                        if b"\x08_airdrop\x04_tcp" in packet.data.lower():
                            airdrop[name] += 1
            except TimeoutError:
                pass
            else:
                raise RuntimeError("iPhone packet stream ended before the window elapsed")
    print("Packets per interface:", dict(counts))
    print("Packets containing the AirDrop service label:", dict(airdrop))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, choices=range(1, 121), default=20, metavar="1..120")
    asyncio.run(inspect(parser.parse_args().seconds))
