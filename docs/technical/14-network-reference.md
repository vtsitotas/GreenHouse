# 14 — Συγκεντρωτικός Πίνακας Δικτύου (Θύρες / Πρωτόκολλα / OSI)

Ενιαία αναφορά κάθε ζεύξης στο σύστημα. Λεπτομέρεια κάθε γραμμής στο
αντίστοιχο έγγραφο.

## Πλήρης πίνακας ζεύξεων

| # | Ζεύξη | Θύρα | L1/L2 | L3/L4 | L5-7 (πρωτόκολλο) | Κρυπτογράφηση | Λεπτομέρεια |
|---|---|---|---|---|---|---|---|
| 1 | Αισθητήρας → Γέφυρα | — (radio, όχι TCP/IP θύρα) | 802.11 PHY, ESP-NOW (Action Frames) | καμία (L2-only) | Custom `MeshBeacon`/`MeshDataPacket` | AES-128-CTR (data), plaintext (beacons) | `02`, `03` |
| 2 | Γέφυρα → Mosquitto (τοπικό) | TCP/8883 | 802.11 WiFi STA (WPA2, σπιτικό router) | TCP | MQTT 3.1.1 πάνω από TLS | TLS 1.2, `setInsecure()` (χωρίς επικύρωση) | `04`, `10 §3` |
| 3 | Εφαρμογή → Mosquitto (LAN) | TCP/8883 | WiFi (τοπικό δίκτυο) | TCP | MQTT πάνω από TLS | TLS, `onBadCertificate=true` (χωρίς επικύρωση) | `13 §5`, `10 §4` |
| 4 | Mosquitto → HiveMQ Cloud | TCP/8883 | Ethernet/WiFi → Internet | TCP | MQTT πάνω από TLS 1.2 | Πλήρης TLS επικύρωση (δημόσιο CA store) | `08`, `10 §5` |
| 5 | Εφαρμογή → HiveMQ Cloud (remote) | TCP/8883 | Κινητό δίκτυο/WiFi → Internet | TCP | MQTT πάνω από TLS | `onBadCertificate=true` (ίδιο με #3) | `13 §5` |
| 6 | Εφαρμογή → Portal (LAN μόνο) | TCP/80 | WiFi (τοπικό) | TCP | HTTP/1.1 (Flask) | **Καμία** (plaintext HTTP) | `09` |
| 7 | Κάμερα → cam_bridge.py | TCP/8090 | WiFi (τοπικό) | TCP | HTTP/1.1 (Flask) | **Καμία** | `12 §2, §7` |
| 8 | cam_bridge.py → Κάμερα (poll/delete) | TCP/80 (στην κάμερα) | WiFi (τοπικό) | TCP | HTTP/1.1 | **Καμία** | `12 §7` |
| 9 | Internal loopback (weather/recorder/simulator → Mosquitto) | TCP/1883 | loopback interface | TCP | MQTT plaintext, anonymous | Καμία (network-isolated, `127.0.0.1` bind) | `05 §2` |
| 10 | Εφαρμογή/Γέφυρα → Mosquitto (websocket, **αχρησιμοποίητο**) | TCP/9001 | WiFi | TCP | MQTT over WebSocket + TLS | TLS | `05 §3` |
| 11 | Πρώτη εγκατάσταση: Κινητό → Pi AP | — | 802.11 (ανοιχτό δίκτυο, χωρίς WPA) | TCP | HTTP (captive portal) | **Καμία** | `09 §2-4` |
| 12 | weather.py → Open-Meteo | TCP/443 | Ethernet/WiFi → Internet | TCP | HTTPS (`urllib.request`) | TLS (δημόσιο API, standard library validation) | `11 §1` |
| 13 | push.py → Firebase Cloud Messaging | TCP/443 | Ethernet/WiFi → Internet | TCP | HTTPS (`firebase_admin` SDK) | TLS | `13 §10` |
| 14 | Avahi mDNS | UDP/5353 (multicast) | WiFi (τοπικό) | UDP | mDNS/DNS-SD (RFC 6762/6763) | Καμία | `09 §8` |
| 15 | AP DHCP/DNS (setup mode) | UDP/67-68 (DHCP), UDP/53 (DNS) | WiFi hotspot | UDP | DHCP, DNS (NetworkManager dnsmasq-shared) | Καμία | `09 §2` |

## Χάρτης θυρών ανά συσκευή

### Raspberry Pi
| Θύρα | Υπηρεσία | Ποιος συνδέεται |
|---|---|---|
| 80 | `greenhouse-portal` (Flask) | Κινητό, μόνο LAN/AP |
| 1883 | Mosquitto, loopback plaintext | `weather.py`, `recorder.py`, `cam_bridge.py`, `simulator.py` — όλα τοπικά |
| 8883 | Mosquitto, TLS | Γέφυρα ESP32, Εφαρμογή (LAN) |
| 8090 | `greenhouse-cam-bridge` (Flask) | ESP32-CAM (POST snapshots) |
| 9001 | Mosquitto, WebSocket+TLS | Κανένας ενεργός client σήμερα |
| 22 | SSH (OpenSSH, εκτός εμβέλειας project code) | Διαχείριση/deploy |

### ESP32 συσκευές
| Συσκευή | Θύρες server | Client προς |
|---|---|---|
| Bridge | Καμία (καθαρά client) | Mosquitto :8883 (MQTT client) |
| Edge nodes (C3/WROOM) | Καμία (ESP-NOW μόνο, χωρίς IP stack χρήσης) | — |
| ESP32-CAM | 80 (WebServer: `/capture`, `/stream`, `/event/<id>`) | `greenhouse.local:8090` (POST snapshots) |

### Cloud
| Υπηρεσία | Θύρα | Ρόλος |
|---|---|---|
| HiveMQ Cloud | 8883 (TLS) | Δημόσιος relay broker, single-tenant credentials |
| Open-Meteo | 443 (HTTPS) | Δημόσιο REST API, χωρίς κλειδί |
| Firebase Cloud Messaging | 443 (HTTPS) | Push notification delivery |

## OSI Layer breakdown — σύνοψη ανά υποσύστημα

| Υποσύστημα | L1 Physical | L2 Data Link | L3 Network | L4 Transport | L5-7 Application |
|---|---|---|---|---|---|
| Mesh αισθητήρων | 2.4GHz ISM, 802.11 PHY | ESP-NOW (Action Frames, MAC addressing) | **Ανύπαρκτο** (custom rank routing στο app layer) | **Ανύπαρκτο** (single ACK, καμία retransmission) | Custom `MeshBeacon`/`MeshDataPacket` structs |
| Γέφυρα↔Router↔Pi | 2.4GHz 802.11 b/g/n | 802.11 (association, WPA2) | IPv4 | TCP | MQTT 3.1.1 + TLS 1.2 |
| Pi↔HiveMQ | Ethernet/WiFi (physical uplink) | Ethernet/802.11 | IPv4/IPv6 (Internet routing) | TCP | MQTT + TLS 1.2 (πλήρης επικύρωση) |
| Κινητό↔Pi (HTTP) | 802.11 | 802.11 | IPv4 | TCP | HTTP/1.1, Flask/Jinja2 |
| Κινητό↔Cloud | Κινητό δίκτυο/WiFi | LTE/5G ή 802.11 | IPv4/IPv6 | TCP | MQTT/HTTPS |

Σημειώσεις:
- Το mesh layer (αισθητήρες) είναι το **μοναδικό** σημείο του συστήματος
  όπου δεν υπάρχει καθόλου IP στοίβα — καθαρό L2 πρωτόκολλο. Όλα τα
  υπόλοιπα (γέφυρα προς τα πάνω, Pi, εφαρμογή, cloud) είναι κλασικό
  TCP/IP.
- Δεν υπάρχει UDP οπουδήποτε στη ροή δεδομένων αισθητήρα→εφαρμογή· το
  μόνο UDP στο σύστημα είναι υποδομής (mDNS ανακάλυψη, DHCP/DNS στο setup
  mode) — καμία πραγματική μέτρηση/εντολή ταξιδεύει ποτέ πάνω από UDP.
