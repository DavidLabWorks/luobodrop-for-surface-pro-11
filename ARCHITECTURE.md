# Architecture

The MT7922 WiFiCx driver keeps the station connection to the access point and handles AWDL traffic on the same primary channel. WiFiCx and NetAdapterCx expose the ordinary Windows network path. The project control interface exposes the AWDL receive and transmit paths to the user-mode relay.

```text
Wi-Fi access point <--> MT7922 <--> WiFiCx / NetAdapterCx <--> Windows networking
                            |
                            +--> AWDL driver path <--> Wintun IPv6 <--> TCP/TLS file transfer
                                       ^
                                       |
                                  Nearby iPad
```

The relay uses Bluetooth to trigger discovery and AWDL for link-local IP traffic. The file client and server use TLS with the AirDrop discovery, acceptance and upload operations. The Windows panel drives the same scripts and consumes their progress events. The peer address is provided by the operator.

The receive path uses HE, Block Ack and wireless reordering, A-MSDU decoding, and a separate TCP coalescing step. The send baseline uses early Block Ack setup, automatic width and rate selection, HE with two spatial streams, two-subframe A-MSDU, 16-packet batch submissions and a 4 MiB Wintun ring. The reference receive profile uses a 256-record driver queue and fetches up to 32 records in a batch. These limits describe different stages of the data path.

Ordinary Wi-Fi and AWDL share radio airtime. The benchmark probes establish sampled connectivity during the transfer. They do not characterize continuous latency or all router configurations.
