# Third-party notices

LuoboDrop project code used in the combined driver and transfer tools is released under [GPL-3.0-or-later](LICENSE). Third-party material retains the licenses listed below. The November 24, 2026 source preview replaces the development driver’s Microsoft sample ring iterator with WDK API calls.

| Component | Location and terms |
| --- | --- |
| PulseIt Block Ack reorder | `tools/pulseit-ba/`, from [gsl0001/windows-airdrop at 2b336953460820e3880bb83cef8bd4acc15cf584](https://github.com/gsl0001/windows-airdrop/tree/2b336953460820e3880bb83cef8bd4acc15cf584). GPL-3.0-or-later. The code is compiled into the kernel driver through `src/pulseit-ba-kernel.c`. |
| WinAWDL AirDrop | `tools/winawdl/airdrop/`, from [raph559/winawdl at 0db7031589dc2bca03c0893db341176376962047](https://github.com/raph559/winawdl/tree/0db7031589dc2bca03c0893db341176376962047). The MIT AirDrop sources include local receiver, sender, mDNS, transfer and test changes. The Bluetooth beacon script is included with WinAWDL's root MIT notice. |
| WinAWDL TCP merger | `tools/winawdl/awdl/win/tcp_merge.c` and its header, from the same upstream commit. GPL-3.0-or-later. The Python receiver loads the compiled DLL in process. |
| NetAdapterCx | The source preview uses the WDK packet and fragment API for ordinary Wi-Fi. [Microsoft NetAdapterCx ring documentation](https://learn.microsoft.com/en-us/windows-hardware/drivers/netcx/introduction-to-net-rings). |
| Linux mt76 / MediaTek | The two known Connac2 reference files match [the pinned mt76 fork](https://github.com/shaecodes-wilde/mt76/tree/02b6c9a1ef87e55f2ccf90739e5dbcab55390cc7). See the [BSD-3-Clause-Clear notice](licenses/mt76-connac2.txt) for the identified portions. Additional layouts, descriptors and MCU logic remain under provenance review. |
| Owly AWDL reference | The PSF/MIF fields, election and channel sequence in the Windows implementation were informed by [Owly at 21171d8f9a08bfa2c625a1fa338e877a67913e54](https://github.com/t4t5/owly/tree/21171d8f9a08bfa2c625a1fa338e877a67913e54). The [original MIT notice](licenses/owly-MIT.txt) is retained. The driver build does not compile Owly Rust code. |
| WinAWDL VHT reference | `src/tx.c` uses the VHT capability values and element layout documented in [WinAWDL frame_fixup.c](https://github.com/raph559/winawdl/blob/0db7031589dc2bca03c0893db341176376962047/awdl/win/frame_fixup.c) (GPL-3.0-or-later). The reference file is outside the build. The degree of derivation of the local encoder remains under review. |
| Wintun and MediaTek firmware | Separately obtained local inputs. See their upstream terms and version details in [BUILD.md](BUILD.md). |
| Microsoft tools and libraries | Obtain the WDK and SDK separately on Microsoft's terms. Their binaries and test certificates are excluded. |

## WinAWDL source inventory

The received TCP stream is processed by the Python relay. The fast receive profile loads the WinAWDL merger through the project's ctypes adapter in the same process. The C source and header match the pinned upstream Git blobs and carry GPL-3.0-or-later. Keep the applicable GPL text and provide the combined receiver source under compatible terms with the adapter, interface definitions and build scripts when distributing the combined receiver. For a future binary release, provide source corresponding to that exact build. [GPLv3 sections 1, 5 and 6](https://www.gnu.org/licenses/gpl-3.0.html)

The locally modified files from the MIT AirDrop subtree are `airdrop/mdns.py`, `airdrop/receiver.py`, `airdrop/sender.py`, `airdrop/transfer.py`, `airdrop/tests/test_receiver.py`, `airdrop/tests/test_transfer.py`. Paths in this list are relative to the WinAWDL subtree. Keep both original MIT notices with their respective files. The WinAWDL GPL-2.0-only radio component is outside the build and source snapshot.

## MediaTek firmware inputs

The two upstream MT7922 Wi-Fi files and the license text were verified against the same Linux Firmware revision. The MediaTek terms allow use and redistribution of the original files for devices containing MediaTek chipsets, subject to their stated GPL/LGPL restriction. The source snapshot uses separately obtained inputs and locally produces the driver image and the extracted CLC. Distribution rights for those generated files have not been established. [Pinned Linux Firmware commit](https://kernel.googlesource.com/pub/scm/linux/kernel/git/firmware/linux-firmware/+/2452612955cd7fd31f4cca30f129b9b9959c8da5) · [MediaTek firmware terms](https://gitlab.com/kernel-firmware/linux-firmware/-/raw/2452612955cd7fd31f4cca30f129b9b9959c8da5/LICENSES/LICENCE.mediatek)

The full license texts for PulseIt and the included WinAWDL sources are in their component directories. Other source fragments still require component-level provenance verification.
