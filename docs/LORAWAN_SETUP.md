# LoRaWAN Setup Runbook

Hardware and OS bring-up steps for the multi-greenhouse LoRaWAN extension:
one RAK3172-E per remote site talking EU868 LoRaWAN back to a single shared
gateway Pi, which forwards to the existing MQTT/app stack under
`greenhouse/sites/<site_id>/...`. Background, design rationale and the full
task list this runbook was extracted from live in
[`docs/superpowers/plans/2026-09-23-lorawan-multi-greenhouse.md`](superpowers/plans/2026-09-23-lorawan-multi-greenhouse.md)
and the spec at
[`docs/superpowers/specs/2026-09-14-multi-site-lorawan-cellular-design.md`](superpowers/specs/2026-09-14-multi-site-lorawan-cellular-design.md).
These are hardware/OS procedures performed by hand on real devices — nothing
here is automated by `install.sh`.

## Task H1: Remote site (per greenhouse)

**BOM:** RAK3172 Evaluation Board, **EU868** variant (`RAK3172-E`, includes CH340 USB-serial) · 868 MHz ~3 dBi antenna (u.FL/SMA per board variant) · micro-USB OTG adapter + micro-USB cable. No SIM, no internet.

1. Plug the RAK3172-E into the Zero W's **inner** micro-USB port (data/OTG; the outer one is power-only) via the OTG adapter.
2. `sudo cp pi/udev/99-greenhouse-lora.rules /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger`; verify `ls -l /dev/lora`.
3. Sanity: `python3 -c "import serial; s=serial.Serial('/dev/lora',115200,timeout=1); s.write(b'AT\r\n'); print(s.read(64))"` → contains `OK`.
4. In ChirpStack (gateway, Task H2): create the device (OTAA, device profile EU868 / LoRaWAN 1.0.x / Class C), copy DevEUI, JoinEUI(AppEUI) and AppKey into `/etc/greenhouse/lora.json` (template `pi/config/lora.json.example`, mode 0600).
5. Install and enable `greenhouse-lora-uplink.service`; `journalctl -u greenhouse-lora-uplink -f` shows `[lora] joined`.

## Task H2: Gateway (one per network)

**BOM:** Raspberry Pi 4 (≥ 2 GB) + official 5 V/3 A PSU · RAK2287 SPI (EU868) + RAK2287/RAK5146 Pi HAT · 868 MHz ~6 dBi outdoor antenna, **mounted as high as possible** (report §21.7: range is set by antenna height, `d ≈ 3.57(√h₁+√h₂)` km) + low-loss coax + lightning arrestor · SIM7600G-H **USB** modem + LTE antenna + M2M SIM · powered USB hub (≥ 2.5 A).

1. Raspberry Pi OS **64-bit**; install the greenhouse stack as on any site (`pi/install.sh`).
2. `/boot/firmware/config.txt`: `dtparam=spi=on` and `dtoverlay=uart3`. Reboot. Rewire the ESP32 bridge: ESP32 GPIO4 (TX) → Pi **GPIO5 / pin 29** (RXD3); ESP32 GPIO5 (RX) ← Pi **GPIO4 / pin 7** (TXD3); GND common. Find the node with `ls -l /dev/ttyAMA*` and set `Environment=GREENHOUSE_SERIAL_PORT=/dev/ttyAMA<N>` in `greenhouse-serial-bridge.service` (Task S5). Verify `journalctl -u greenhouse-serial-bridge` shows heartbeats.
3. Mount the RAK2287 on the Pi HAT, the HAT on the header. Pins used by the HAT (datasheet): SPI0 GPIO8–11, RESET GPIO17, GPIO7, GPS on GPIO14/15, GPS reset GPIO25, standby GPIO12 — none overlap UART3.
4. Install per the ChirpStack Debian/Ubuntu guide: `redis-server`, `chirpstack-concentratord` (SX1302 build, EU868 config, reset pin 17, SPI `/dev/spidev0.0`), `chirpstack-mqtt-forwarder` (Concentratord backend), and ChirpStack **SQLite** build (≥ 4.10.1). Point ChirpStack and the forwarder at the local Mosquitto with a dedicated user; add ACL lines `topic readwrite eu868/#` for the forwarder/ChirpStack user and `topic read application/#` + `topic write application/+/device/+/command/down` + `topic readwrite greenhouse/sites/#` for the `lora-gateway` user.
5. In ChirpStack: add the gateway (EUI from Concentratord log), region eu868; create the application (copy its UUID into `/etc/greenhouse/lora_sites.json`) and one device per remote site (DevEUI → site id in `sites`).
6. Enable `greenhouse-lora-gateway.service`.

## Task H3: LTE uplink on the gateway

1. Modem on the powered hub. With `minicom -D /dev/ttyUSB2` (AT port; confirm with `ls /dev/ttyUSB*`) send `AT+CUSBPIDSWITCH=9011,1,1`; after it re-enumerates, `ip link` shows `usb0`.
2. `nmcli con add type ethernet ifname usb0 con-name lte ipv4.method auto ipv4.route-metric 50` and bring it up; `curl -4 https://www.google.com -o /dev/null -w '%{http_code}'` → 200 over `usb0`.
3. Confirm `greenhouse-hivemq-bridge` reconnects over LTE and that `greenhouse/sites/<site>/…` topics appear on HiveMQ.
4. Static-IP-direct path stays blocked on the carrier question in spec §Open risks.

## Installing the LoRa services

`pi/install.sh` installs the core greenhouse services by explicit name and
deliberately does **not** install `greenhouse-lora-uplink.service` or
`greenhouse-lora-gateway.service` — they're opt-in per site (a remote site
runs only the uplink service, the gateway runs only the gateway bridge).
Install and enable whichever one applies to the unit by hand, as covered in
Tasks H1 and H2 above:

```
sudo cp pi/systemd/greenhouse-lora-uplink.service /etc/systemd/system/   # remote site
sudo cp pi/systemd/greenhouse-lora-gateway.service /etc/systemd/system/  # gateway
sudo systemctl daemon-reload
sudo systemctl enable --now greenhouse-lora-uplink    # or greenhouse-lora-gateway
```
