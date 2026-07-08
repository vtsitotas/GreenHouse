# Αρχιτεκτονική Συστήματος — Διάγραμμα Ροής

**Τελευταία ενημέρωση:** 2026-07-08

Ένα ενιαίο, απλοποιημένο διάγραμμα Mermaid: πρώτη εγκατάσταση, ροή δεδομένων από
τους αισθητήρες μέχρι την εφαρμογή, και η βάση ιστορικού — όλα σε ένα, χωρίς
περιττή λεπτομέρεια, για γρήγορη κατανόηση/παρουσίαση (π.χ. στη διπλωματική).

Σημειώσεις ακρίβειας (λάθη που κυκλοφορούν σε παλιότερα σχέδια/έγγραφα):

- Η εφαρμογή συνδέεται με **MQTT TCP TLS στη θύρα 8883** — ΟΧΙ με WebSockets στην
  9001 (δοκιμασμένο και σπασμένο: bug του `mqtt_client` 10.x με Mosquitto 2.x).
- Η γέφυρα (gateway) είναι **ασύρματη** (ESP-NOW → WiFi/MQTT) — καμία σύνδεση USB
  serial στο Pi.
- Οι κόμβοι αισθητήρων μιλούν **απευθείας στη γέφυρα** (single-hop ESP-NOW).
- Η απομακρυσμένη πρόσβαση γίνεται μέσω **HiveMQ Cloud** — όχι Tailscale.
- Τα γραφήματα ιστορικού δουλεύουν **μόνο εντός LAN** (το HTTP :80 δεν
  αναμεταδίδεται μέσω HiveMQ).
- Κάθε μονάδα Pi παράγει στην πρώτη εκκίνηση **δικά της** μοναδικά: TLS
  πιστοποιητικά, κωδικό MQTT, κωδικό λειτουργικού, SSID `Greenhouse-XXXX` — γι'
  αυτό το κλωνοποιημένο SD image είναι ασφαλές για μαζική παραγωγή.

---

```mermaid
flowchart TB
    classDef hw fill:#e1f5fe,stroke:#0288d1,stroke-width:2px;
    classDef sw fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px;
    classDef db fill:#fffde7,stroke:#f9a825,stroke-width:2px;
    classDef cloud fill:#fff3e0,stroke:#f57c00,stroke-width:2px;
    classDef client fill:#e8f5e9,stroke:#388e3c,stroke-width:2px;
    classDef setup fill:#f0f4c3,stroke:#afb42b,stroke-width:2px,stroke-dasharray: 5 5;

    %% -- Φάση 1: Πρώτη εγκατάσταση --
    subgraph Setup ["Φάση: Πρώτη εγκατάσταση"]
        direction LR
        S_Phone["Κινητό χρήστη"]:::client
        S_AP["Pi σε Setup mode<br/>hotspot Greenhouse-XXXX<br/>+ captive portal (φόρμα WiFi)"]:::setup
        S_Phone -->|"1. Σύνδεση & αποστολή WiFi"| S_AP
        S_AP -->|"2. Επανεκκίνηση, σύνδεση<br/>σε οικιακό WiFi"| S_Phone
    end

    %% -- Θερμοκήπιο --
    subgraph Field ["Θερμοκήπιο — κόμβοι μπαταρίας/ηλιακού"]
        direction LR
        Nodes["Κόμβοι ζωνών 1–N<br/>ESP32-C3 (θερμ./υγρασία/φως)"]:::hw
        Bridge["Γέφυρα (Gateway)<br/>ESP32"]:::hw
        Nodes -->|"ESP-NOW · low power"| Bridge
    end

    %% -- Raspberry Pi --
    subgraph Pi ["Raspberry Pi Zero W — τοπικός διακομιστής"]
        direction TB
        Broker["Mosquitto Broker<br/>1883 εσωτερικά · 8883 TLS εξωτερικά"]:::sw
        Weather["greenhouse-weather<br/>πρόγνωση, κανόνες αυτοματισμού"]:::sw
        Recorder[("greenhouse-recorder → SQLite<br/>ανά λεπτό (90 ημ.) · ανά ώρα (2 έτη)<br/>μαζική εγγραφή, ποτέ ανά πακέτο")]:::db
        Portal["greenhouse-portal — Flask :80<br/>/pair · /api/history · captive portal"]:::sw

        Broker -->|"topics αισθητήρων"| Recorder
        Broker <-->|"αυτοματισμοί"| Weather
        Recorder -->|"ερωτήματα ιστορικού"| Portal
    end

    %% -- Cloud & Εφαρμογή --
    subgraph External ["Δίκτυο & τελικός χρήστης"]
        direction LR
        HiveMQ["HiveMQ Cloud<br/>MQTT broker αναμετάδοσης"]:::cloud
        App["Flutter App<br/>Dashboard · Ιστορικό · Ρυθμίσεις"]:::client
    end

    S_AP -->|"3. GET /pair<br/>διαπιστευτήρια MQTT + TLS"| Portal
    Bridge -->|"MQTT μέσω WiFi"| Broker
    Broker <-->|"αμφίδρομη αναμετάδοση topics"| HiveMQ
    Broker <-->|"live δεδομένα · MQTT TLS :8883<br/>(εντός LAN)"| App
    HiveMQ <-->|"live δεδομένα · MQTT TLS<br/>(εκτός σπιτιού)"| App
    Portal -->|"γραφήματα ιστορικού · HTTP :80<br/>(μόνο εντός LAN)"| App
```
