# meshsim — simulator / calculator του mesh

Όλες οι εντολές τρέχουν από τον φάκελο `sim/` (μόνο Python 3, χωρίς εξαρτήσεις).

| Αρχείο | Τι είναι |
|---|---|
| [`PARAMETERS.md`](PARAMETERS.md) | Όλες οι παράμετροι με πηγή (παράγεται από τον κώδικα) + μητρώο εξαρτημάτων + όλες οι μεταβλητές run |
| `sim/runs/<run>/` | Κάθε run: `config.json`, `results.json`, `report.md`, `meta.json` (git commit, firmware hash) |
| `sim/runs/index.csv` | Μία γραμμή ανά run για σύγκριση |
| Spec | `docs/superpowers/specs/2026-09-28-mesh-simulator-design.md` |

## Dashboard

```bash
python -m meshsim serve
```

Ανοίγει το `http://127.0.0.1:8765/`. Στο αριστερό πάνελ αλλάζει κάθε μεταβλητή (οι αλλαγμένες φαίνονται με πράσινο).
Τα κουμπιά «Υπολογισμός» και «Προσομοίωση (DES)» τρέχουν τον engine στον τοπικό server, και **κάθε run
καταγράφεται στο `sim/runs/`** όπως από το CLI. Tabs: Ενέργεια, Δίκτυο, Βελτιώσεις, DES (με timeline καταστάσεων
και ουρά γέφυρας), Runs (όλα τα runs του `sim/runs/index.csv`, εισαγωγή/εξαγωγή JSON).

Η δημοσιευμένη εκδοχή (artifact) δεν έχει server: αν δεν φορτώσει ο Python engine στο browser, δείχνει τα
έτοιμα παραδείγματα (`python -m meshsim web-examples` → `sim/web/examples.json`) και ό,τι `results.json` εισαχθεί.

## Εντολές

```bash
python -m meshsim keys          # κάθε μεταβλητή: default + τι σημαίνει
python -m meshsim presets       # firmware_today, stress_50x10, greenhouse, nursery, field
python -m meshsim calc --preset greenhouse
python -m meshsim improve --preset greenhouse
python -m meshsim sweep --preset greenhouse --vary timing.T_s=900,1800 --vary hw.climate_sensor=dht22,sht40
python -m meshsim des --preset greenhouse --set des.cycles=20 --set des.seeds=5
```

`des` = προσομοίωση διακριτών γεγονότων: κάθε εκπομπή, backoff, σύγκρουση, retry, buffer και αφύπνιση στον
χρόνο. Βγάζει μετρημένα PDR, καθυστέρηση (διάμεση/μέση/p95), συγκρούσεις ανά προσπάθεια, απορρίψεις ανά αιτία,
πληρότητα buffers, διπλότυπα στο Pi, και τα συγκρίνει ανά rank με τον calculator. Με `des.seeds>1` δίνει 95 % CI.

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
`docs/analysis/cart_sim.py`). Η τοπολογία είναι ισορροπημένο layered δέντρο.

Ο DES συμφωνεί με τον calculator στην ενέργεια (±2–5 %) και στο PDR όταν δεν υπάρχει ανταγωνισμός. Εκεί που
διαφωνούν, μετράει ο DES: οι **συγκρούσεις σε συγχρονισμένα bursts** (όλοι οι κόμβοι ενός rank μέσα σε jitter
100 ms, με hidden terminals) είναι πολύ υψηλότερες από την εκτίμηση κλειστού τύπου. Απλοποιήσεις του DES
(γραμμένες στην αρχή του `des.py`): στατικό δέντρο χωρίς re-parenting, τα ACK της γέφυρας δεν περνούν από το
μοντέλο καναλιού, τα beacons δεν προσομοιώνονται. Τα orphan sweeps (σπάνια) χρεώνονται ενεργειακά με την
αναμενόμενη συχνότητα (`des.sweep_energy=expected`).
