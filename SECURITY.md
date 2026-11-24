# Security

LuoboDrop uses experimental kernel code and exposes nearby network and file-transfer paths. Run test builds only in a controlled environment. Use a trusted peer and accept only an expected request. The current TLS path uses local self-signed identities. It does not establish an Apple Account or contact identity.

Report security issues privately to the maintainer through an existing private channel. If you need contact details, open a public issue requesting a private contact without including vulnerability details. Include the driver version, Windows build, affected operation and a minimal description or synthetic reproduction. Remove peer addresses, device identifiers and private content before sharing diagnostics, and keep unredacted traces local.

The release security review covers driver control access, bounds checking, address and firewall scope, filename handling, and cleanup after transfers. [Build and recovery](BUILD.md) describes the test driver and vendor-driver recovery boundaries.
