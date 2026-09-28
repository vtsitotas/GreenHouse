# meshsim — simulator / calculator του mesh

Όλες οι εντολές τρέχουν από τον φάκελο `sim/` (μόνο Python 3, χωρίς εξαρτήσεις).

| Αρχείο | Τι είναι |
|---|---|
| [`PARAMETERS.md`](PARAMETERS.md) | Όλες οι παράμετροι με πηγή (παράγεται από τον κώδικα) + μητρώο εξαρτημάτων + όλες οι μεταβλητές run |
| `sim/runs/<run>/` | Κάθε run: `config.json`, `results.json`, `report.md`, `meta.json` (git commit, firmware hash) |
| `sim/runs/index.csv` | Μία γραμμή ανά run για σύγκριση |
| Spec | `docs/superpowers/specs/2026-09-28-mesh-simulator-design.md` |

## Εντολές

```bash
python -m meshsim keys          # κάθε μεταβλητή: default + τι σημαίνει
python -m meshsim presets       # firmware_today, stress_50x10, greenhouse, nursery, field
python -m meshsim calc --preset greenhouse
python -m meshsim improve --preset greenhouse
python -m meshsim sweep --preset greenhouse --vary timing.T_s=900,1800 --vary hw.climate_sensor=dht22,sht40
```

Οποιαδήποτε μεταβλητή αλλάζει με `--set key=value` (πολλές φορές). `--label όνομα` δίνει όνομα στο run,
`--no-log` δεν το καταγράφει.

## Παραδείγματα ερωτήσεων

**«Πόση αυτονομία έχει ένας edge κόμβος;»**
```bash
python -m meshsim calc --preset greenhouse --set hw.solar=none
```
→ `leaf ... days without sun` και ανά rank στο `report.md` (πίνακας «Ανά rank» + «Πού πάει η ενέργεια»).

**«Και με SHT40, light sleep και μεγαλύτερη μπαταρία;»**
```bash
python -m meshsim calc --preset greenhouse --set hw.climate_sensor=sht40 --set hw.warmup_mode=light_sleep --set hw.battery=lifepo4_26650_3000
```

**«Ποιο είναι το θεωρητικό όριο;»** — κάθε `report.md` έχει ενότητα «Θεωρητικά όρια» (ιδανικό φύλλο: μόνο chip,
SHT40, light sleep, ένα frame).

**«Τι κερδίζω από κάθε βελτίωση;»** — `improve`: κάθε βελτίωση hardware/firmware/γέφυρας μόνη της και όλες μαζί.

**«T1 ή T2 στα 50×10;»**
```bash
python -m meshsim sweep --preset stress_50x10 --set scheme.max_ttl=64 --vary scheme.technique=T1-ladder,T2-window
```

## Τι είναι μετρημένο και τι όχι

Κάθε τιμή στο `PARAMETERS.md` έχει `kind`. Οι σημαντικές **μη μετρημένες** που επηρεάζουν πολύ τα αποτελέσματα:

- `sync.step_per_300s` — περιπλάνηση ρολογιού (Gate 0 run 2 θα τη μετρήσει). Κυριαρχεί στην ενέργεια των relays.
- `pi.process_s` — χρόνος επεξεργασίας ανά frame στο Pi. Κυριαρχεί στο παράθυρο της T2 σε μεγάλα δίκτυα.
- Sleep floor της πλακέτας (55 µA με LDO ή ~10 µA χωρίς) — μέτρηση με PPK2.
- `radio.mac_retry`, `timing.radio_init_s`, χρόνος σταθεροποίησης του αισθητήρα εδάφους.

## Όρια του calculator

Ο calculator είναι ντετερμινιστικός: οι στοχαστικές επιδράσεις (χαμένα wakes, απώλειες link, συγκρούσεις)
μπαίνουν ως αναμενόμενες τιμές κλειστού τύπου ή από το Monte Carlo του ζεύγους ρολογιών (`clock.py`, port του
`docs/analysis/cart_sim.py`). Η τοπολογία είναι ισορροπημένο layered δέντρο. Ο DES (επόμενο βήμα) ελέγχει
τα ίδια νούμερα με πλήρη προσομοίωση χρόνου.
