<!-- =============================================================================
HYDRA-UMC-BRIDGE-UAV - UAV coordination bridge
Copyright (C) JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

<p align="center">
  <img src="images/HYDRA_UMC_BANNER.svg" alt="HYDRA-UMC-BRIDGE-UAV banner" width="100%">
</p>

# 🚁 HYDRA-UMC-BRIDGE-UAV

<p align="center"><a href="README.md">🇺🇸 English</a> | <a href="README_spa.md">🇪🇸 Español</a> | <a href="README_fra.md">🇫🇷 Français</a> | 🇮🇹 <b>Italiano</b> | <a href="README_deu.md">🇩🇪 Deutsch</a> | <a href="README_zho.md">🇨🇳 简体中文</a> | <a href="README_jpn.md">🇯🇵 日本語</a> | <a href="README_por.md">🇵🇹 Português</a></p>

### 🔗 Confine di coordinamento privo di dipendenze tra HYDRA-UMC e gli UAV con telecamera

<p align="center">
  <img src="https://img.shields.io/badge/License-GPL%203.0--or--later-blue.svg" alt="GPL 3.0 or later">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB.svg" alt="Python">
  <img src="https://img.shields.io/badge/Safety-Fails%20Closed-red.svg" alt="Safety">
  <img src="https://img.shields.io/badge/Tests-139%20passing-2ea44f.svg" alt="Tests">
  <img src="https://img.shields.io/badge/Version-0.1.5-0f6cbd.svg" alt="Version">
  <img src="https://img.shields.io/badge/Maturity-functional-0F6CBD.svg" alt="Maturity">
</p>

---

> **Verifica di onestà - cosa funziona davvero oggi:** il nucleo di richieste di volo senza dipendenze (`coordinator.py` con `UavCoordinator`, che fa passare ogni dispatch attraverso il vero `evaluate_job()` di `HYDRA-UMC-SDK`), il watchdog deterministico di perdita di collegamento tramite heartbeat (`heartbeat.py` con `HeartbeatMonitor`), e il vero trasporto di comandi MAVLink (`mavlink_transport.py` con `MavlinkFlightControl`) sono reali e coperti da 84 test unitari superati (`python tools/build_test.py` - `test_coordinator.py`, `test_heartbeat.py`, `test_mavlink_transport.py`, più `test_mavlink_emulator.py` che esegue il bridge contro un emulatore di autopilota MAVLink fedele al protocollo ma scritto a mano, non uno reale). Nulla di tutto ciò è stato testato contro una vera installazione `pymavlink`, un vero collegamento radio/telemetria, o un UAV/controller di volo fisico - la finta connessione MAVLink propria di `test_mavlink_transport.py` sostituisce interamente `pymavlink` (la libreria reale non deve nemmeno essere installata perché questi test passino), e non esiste ancora un comando `run` dal vivo perché nessun trasporto MAVLink/OSDK reale è stato scelto o validato. Vedi "Roadmap e limiti noti" qui sotto, che lo dice già chiaramente, e `CHANGELOG.md` per cosa è stato esattamente consegnato finora.

> **Stato di sicurezza (safety e security):** questo ecosistema non è un sistema di sicurezza certificato. Non esiste ancora un percorso E-STOP hardware, né limiti o interblocchi imposti dal firmware, né un comando di movimento verificato; le immagini firmware sono controllate con CRC32 e un HMAC-SHA256 a chiave condivisa, non con una firma asimmetrica; TLS e TLS reciproco sono opzionali e disattivati per impostazione predefinita; nessuna scheda propria è stata ancora assemblata o provata. Leggi la [base di sicurezza](https://github.com/JuanenRac/HYDRA-UMC-DOCS/blob/main/docs/SECURITY_BASELINE.md) e il [modello delle minacce](https://github.com/JuanenRac/HYDRA-UMC-DOCS/blob/main/docs/THREAT_MODEL.md) prima di fidarti di qualsiasi affermazione qui per la sicurezza di una persona.

---

## 📋 In breve

| | |
|---|---|
| Versione | `0.1.5` |
| Maturità | Funzionale |
| Test | 139 (`python tools/build_test.py`) |
| Tecnologia | Python / MAVLink / HYDRA-UMC-SDK |
| Su hardware reale | Solo simulato: provato con emulatori, test double o simulatori; per questa versione non risulta alcuna esecuzione su hardware reale |

**Vai a:** [Panoramica](#-panoramica) · [Architettura](#-architettura) · [Funzionalità](#-funzionalità) · [Sicurezza e protezione](#-sicurezza-e-protezione) · [Interfacce](#-interfacce) · [Configurazione](#-configurazione) · [Avvio rapido](#-avvio-rapido) · [Test](#-test) · [Risoluzione dei problemi](#-risoluzione-dei-problemi) · [Struttura del repository](#-struttura-del-repository) · [Progetti correlati](#-progetti-correlati)

## 🚦 Stato del progetto

Ogni riga è una parte di questo repository e il semaforo dice a che punto è oggi. È scritta in base a ciò che il repository può mostrare (test, compilazioni, esecuzioni), non a ciò che è previsto.

🟢 Fatto e verificato · 🟡 Funziona, con limiti noti · 🟠 Fatto a metà o non ancora verificato · 🔴 Non fatto o non funzionante

| Area | Stato | Cosa significa qui |
|---|---|---|
| Software | 🟡 | Funziona per il suo scopo principale; la verifica di onestà qui sopra elenca ciò che manca ancora |
| Test | 🟢 | 139 test automatici, eseguiti a ogni modifica |
| Hardware reale | 🟠 | Solo simulato: provato con emulatori, test double o simulatori; per questa versione non risulta alcuna esecuzione su hardware reale |
| Documentazione | 🟢 | README in otto lingue, verificato rispetto al manifesto a ogni modifica |

## 🎯 Panoramica

**HYDRA-UMC-BRIDGE-UAV** è il confine di coordinamento di alto livello tra HYDRA-UMC e un drone (UAV) dotato di telecamera, raggiungibile via Wi-Fi, un collegamento radio o una connessione di telemetria cellulare (4G/5G). Valida e inoltra un vocabolario ridotto e con nome di richieste di volo di alto livello (`ARM`, `TAKEOFF`, `GOTO_WAYPOINT`, `HOVER_AND_CAPTURE`, `RETURN_TO_LAUNCH`), ed esegue separatamente un watchdog reale e obbligatorio di heartbeat per la perdita di collegamento. Non calcola mai il controllo di volo o la stabilizzazione, e non può aggirare HYDRA-UMC-SERVER, i limiti dell'MCU, i watchdog o l'E-STOP. Oggi l'unico percorso di ritorno dal veicolo è la conferma del comando (`COMMAND_ACK`); la telemetria non viene letta.

Appartiene alla famiglia **Mobile & Autonomous Bridges** insieme a `HYDRA-UMC-BRIDGE-DROIDS` e `HYDRA-UMC-BRIDGE-AMR`, e condivide lo stesso contratto di lavoro e sicurezza `HYDRA-UMC-SDK` degli **External Automation Bridges** stazionari (CNC, LASER, OPENPNP, PRINTER3D, ROS2).

## 🧩 Ruolo in HYDRA-UMC

**HYDRA-UMC-BRIDGE-UAV** sta tra un UAV con telecamera (MAVLink) e la cella di robot. Riferisce ciò che dice quel lato, interroga la porta condivisa prima di qualsiasi lavoro ausiliario e non assume mai l'autorità della macchina con cui si coordina.

Condivide con ogni bridge il contratto di sicurezza di `HYDRA-UMC-SDK`: un lavoro viene accettato solo se la porta condivisa `evaluate_job()` lo permette, e quella porta fallisce chiusa. Questo bridge non ha un trasporto MQTT; gira come il comando `hydra-umc-bridge-uav run`.

## 🏛️ Architettura

<p align="center">
  <img src="images/architecture.svg" alt="HYDRA-UMC-BRIDGE-UAV architecture" width="100%">
</p>

<details>
<summary>Versione testuale del diagramma (Mermaid)</summary>

```mermaid
flowchart LR
    BRIDGE["BRIDGE-UAV<br/>UavCoordinator.dispatch()"] -- "MAVLink" --> UAV["UAV con telecamera<br/>(Wi-Fi / Radio / telemetria 4G-5G)"]
    UAV -- "COMMAND_ACK" --> BRIDGE
    UAV -- "heartbeat" --> HB["HeartbeatMonitor<br/>.observe() / .state()"]
    HB -- "LOST -> failsafe_action" --> BRIDGE
    BRIDGE -- BridgeJob --> SDK["HYDRA-UMC-SDK<br/>evaluate_job()"]
    SDK -- GateDecision --> BRIDGE
```

</details>

**Perché è fatto così**

* **Perché il watchdog di heartbeat è un modulo a sé, non integrato nel coordinatore.** La perdita di collegamento è una modalità di guasto reale e distinta da "questo lavoro è consentito adesso" - `HeartbeatMonitor` risponde a "posso ancora fidarmi dell'ultima cosa che ho saputo da questo UAV" indipendentemente da qualsiasi lavoro specifico, così può essere verificato in modo continuo (ad es. ad ogni tick di telemetria) e non solo quando un lavoro viene effettivamente inviato.
* **Perché `HeartbeatMonitor` riceve un `now` esplicito invece di leggere un orologio reale.** La nota di architettura incollata da cui è partito questo progetto afferma esplicitamente che un ponte UAV RICHIEDE questo watchdog - l'unico modo per dimostrare il suo comportamento esatto al limite del timeout (non solo "prima o poi scade") senza una suite di test lenta e instabile basata su sleep reali è rendere il tempo un input esplicito e testabile.
* **Perché `COMPLETE` e `ABORT` si risolvono entrambi in `RETURN_TO_LAUNCH`.** Una missione conclusa e un abort di emergenza hanno lo stesso esito reale corretto per un UAV: tornare a casa. Farli confluire nello stesso nome di richiesta nel piano statico (deduplicato, non ripetuto) riflette questo con onestà invece di inventare due verbi diversi per "tornare".
* **Perché l'heartbeat proprio di questo ponte NON è esplicitamente un sostituto del failsafe proprio del controller di volo.** Il firmware di Pixhawk/PX4 e di DJI implementa già un failsafe reale, quasi certificato, di perdita di collegamento a livello di radio/telemetria - l'`HeartbeatMonitor` di questo ponte è un segnale del solo livello di coordinamento per lo stato proprio di HYDRA-UMC, ed entrambi devono esistere in modo indipendente; vedi la porta di accettazione hardware propria di `docs/BRIDGE_GUIDE.md`.
* **Perché in questo repository non c'è ancora un adattatore di trasporto DJI OSDK.** (Il trasporto MAVLink esiste, vedi sopra.) Vincolarsi al protocollo reale di comandi/telemetria di uno specifico controller di volo prima che sia selezionato e testato rischierebbe di incorporare ipotesi che questo nucleo locale privo di dipendenze non può verificare.
* **Come si inserisce nel resto dell'ecosistema.** BRIDGE-UAV si trova tra un UAV reale e `HYDRA-UMC-SDK` → `HYDRA-UMC-SERVER` → sicurezza MCU - è un confine di coordinamento, mai un nodo di controllo di volo, e non può aggirare HYDRA-UMC-SERVER, i limiti dell'MCU, i watchdog o l'E-STOP.

## ✨ Funzionalità

* ✅ **Nucleo reale di richieste di volo, privo di dipendenze:** `UavCoordinator` in `coordinator.py` non ha alcun import MAVLink né di SDK del produttore - è deliberatamente Python semplice, testabile su qualsiasi host senza un UAV reale collegato. *(implementato, testato in `tests/test_coordinator.py`)*
* ✅ **Vocabolario reale di richieste di volo con nome:** `ARM`, `TAKEOFF`, `GOTO_WAYPOINT`, `HOVER_AND_CAPTURE`, `RETURN_TO_LAUNCH`, `LAND` - mai un comando grezzo di assetto/manetta. Un `COMPLETE` di missione normale e un `ABORT` di emergenza si risolvono entrambi nella stessa richiesta reale `RETURN_TO_LAUNCH`. `LAND` è reale e genuinamente distinto - `MAV_CMD_NAV_LAND` contro `MAV_CMD_NAV_RETURN_TO_LAUNCH` di MAVLink (verificato con l'[insieme ufficiale di messaggi comuni MAVLink](https://mavlink.io/en/messages/common.html)) - per il caso reale che il solo RTL non copre: scendere subito, senza prima tornare a casa (ad es. batteria troppo scarica, o percorso di ritorno non sicuro). `emergency_land_request()` lo espone come richiesta autonoma, deliberatamente fuori dal flusso `dispatch()` guidato da `JobPhase` - stesso ragionamento del segnale separato di `HeartbeatMonitor`. *(implementato)*
* ✅ **Un watchdog reale e obbligatorio di heartbeat per la perdita di collegamento:** `HeartbeatMonitor` è una macchina a stati failsafe deterministica, guidata da un `now` esplicito - non legge mai un orologio reale, riporta `LOST` fin dal primo controllo se non è mai stato osservato nulla, e tratta l'istante esatto del timeout come ancora `OK` (solo superarlo davvero fa scattare il failsafe configurato `RETURN_TO_LAUNCH`/`LAND`). *(implementato, testato con una suite deterministica completa di casi limite in `tests/test_heartbeat.py`)*
* ✅ **HEARTBEAT proprio del bridge come stazione di terra:** `GcsHeartbeat` di `gcs_heartbeat.py` invia un `HEARTBEAT` MAVLink di tipo `MAV_TYPE_GCS` in un thread (1 Hz per impostazione predefinita, `HYDRA_UAV_GCS_HEARTBEAT_HZ` tra 0,1 e 10, `0` per spegnerlo), così il failsafe di perdita della stazione dell'autopilota scatta se il bridge muore. Un battito che non si può inviare viene contato e segnalato, mai sollevato come errore. *(implementato, provato con un collegamento simulato in `tests/test_gcs_heartbeat.py`; non ancora provato con un autopilota reale)* `hydra-umc-bridge-uav run --connection <URL>` (o `python -m hydra_umc_bridge_uav run ...`) apre il collegamento MAVLink e mantiene questo heartbeat, stampa una riga di stato ogni pochi secondi e si ferma pulito con Ctrl-C, SIGTERM o `--duration`; non invia altro al veicolo. Provato contro un registratore del collegamento; mai eseguito contro un vero autopilota o SITL.
* ✅ **Porta di sicurezza condivisa, reale:** ogni lavoro inviato tramite `UavCoordinator.dispatch()` viene valutato da `evaluate_job()` del `bridge_contract` di `HYDRA-UMC-SDK`, la stessa porta usata da tutti i ponti fratelli e da HYDRA-UMC-SERVER; una fase produttiva richiede una macchina esterna `IDLE` e una cella HYDRA-UMC `READY`, mentre `ABORT` resta richiedibile durante un guasto. *(implementato)*
* ✅ **Instradamento delle fasi chiuso ed evidenza statica:** una futura fase SDK sconosciuta viene negata. `inspect_request_plan.py` emette il piano statico di richieste di volo di schema `1.1` (che ora include la richiesta autonoma `LAND`) senza aprire alcun trasporto. *(implementato, testato)*
* ✅ **Trasporto reale dei comandi MAVLink:** `mavlink_transport.py`'s `MavlinkFlightControl` invia un dispatch già validato come un vero `COMMAND_LONG`, mappato su un `MAV_CMD` reale e numerato (`MAV_CMD_NAV_TAKEOFF`/`MAV_CMD_DO_REPOSITION`/`MAV_CMD_NAV_LOITER_UNLIM`/`MAV_CMD_IMAGE_START_CAPTURE`/`MAV_CMD_NAV_RETURN_TO_LAUNCH`/`MAV_CMD_NAV_LAND`/`MAV_CMD_COMPONENT_ARM_DISARM`) - un dispatch rifiutato non raggiunge mai la rete. *(implementato, testato in `tests/test_mavlink_transport.py`)*
* ✅ **Riferimento di quota esplicito, inviluppo di volo e richieste di stato sicuro:** `GOTO_WAYPOINT` parte come `COMMAND_INT` (posizione in gradi × 1e7, prua mantenuta) con un `altitude_frame` esplicito - `"relative"` (metri sopra il punto di decollo, predefinito) oppure `"amsl"`; una `FlightEnvelope` opzionale (quota massima e raggio dal punto di partenza, stabiliti dall'impianto) rifiuta ciò che sta fuori; `RETURN_TO_LAUNCH`, `LAND` e il `DISARM` autonomo (mai forzato) vengono inviati anche se la porta li ha rifiutati, e un normale ritorno di `COMPLETE` è accettato qualunque cosa dicano cella e macchina; l'attesa del `COMMAND_ACK` salta le conferme di altri comandi e di altri sistemi e considera `IN_PROGRESS` come non ancora concluso. *(implementato, testato; il formato sul filo è verificato solo contro l'emulatore - mai contro il SITL di PX4/ArduPilot né un autopilota reale, e la quota di `TAKEOFF` resta il riferimento proprio dell'autopilota, perché un `COMMAND_LONG` non porta un riferimento)*
* ✅ **Build/test non mutante:** `build-test.bat`/`.sh` compilano il codice sorgente ed eseguono test unitari deterministici senza cambiare versione o CHANGELOG. *(implementato, vedi Compilazione e rilascio più sotto)*
* 🔜 **Un adattatore di trasporto DJI OSDK** (per una piattaforma non-MAVLink) - introdotto solo dopo che quell'SDK sarà selezionato e testato. *(pianificato)*
* ✅ **Stato osservato della macchina:** `UavCoordinator.dispatch()` accetta lo stato riferito da il pilota automatico, che la porta condivisa usa al posto del `machine_state` scritto nel lavoro (un messaggio che chiunque sul broker può pubblicare); senza un rapporto si usa ancora quanto dichiarato. *(implementato, testato in `tests/test_observed_state.py`)*

## 🔒 Sicurezza e protezione

| Minaccia | Difesa | Dove |
|---|---|---|
| Un lavoro per una cella o macchina che non è in stato sicuro | Ogni lavoro passa dalla porta condivisa `evaluate_job()` dell'SDK, che fallisce chiusa; il bridge non ha una logica propria di accettazione | SDK `bridge_contract` |
| Dati di lavoro smisurati | Al massimo 32 parametri, chiavi fino a 64 caratteri, valori fino a 1024, identificatori fino a 128 (`MAX_PARAMETERS`, `MAX_PARAMETER_KEY_CHARS`, `MAX_PARAMETER_VALUE_CHARS`, `MAX_IDENTIFIER_CHARS`) | SDK `bridge_contract` |
| Un ordine ripetuto o rinviato | Un `IdempotencyStore` ricorda ogni chiave per 24 ore e al massimo 10 000 voci; una ripetizione riceve la prima risposta invece di essere valutata di nuovo | SDK `job_intake` |
| Perdere il collegamento con il veicolo | `HeartbeatMonitor` è una macchina a stati deterministica il cui failsafe è `RETURN_TO_LAUNCH` o `LAND`; il bridge invia il proprio HEARTBEAT di stazione a terra a 1 Hz per impostazione predefinita (da 0,1 a 10 Hz, `HYDRA_UAV_GCS_HEARTBEAT_HZ`) | `heartbeat.py`, `gcs_heartbeat.py` |
| Un ordine che nessuno ha autorizzato | `ARM`, `TAKEOFF`, `GOTO_WAYPOINT` e `HOVER_AND_CAPTURE` richiedono autorizzazione; le richieste di stato sicuro, tranne `DISARM`, sono sempre accettate | `simulated_uav.py` |
| Un messaggio MAVLink falsificato | Firma MAVLink 2 con una chiave di 32 byte (file grezzo o 64 caratteri esadecimali) da `--signing-key-file` o `HYDRA_UMC_BRIDGE_UAV_SIGNING_KEY_FILE`; i messaggi non firmati sono rifiutati a meno di `--signing-allow-unsigned` per un veicolo che non firma ancora | `mavlink_transport.py`, `cli.py` |
| Un riferimento di quota scambiato | Il riferimento di quota è esplicito: `relative` (`GLOBAL_RELATIVE_ALT_INT`) o `amsl` (`GLOBAL_INT`) | `mavlink_transport.py` |

**Cosa non fa mai:** trasforma in veri comandi MAVLink solo le richieste di volo di alto livello approvate dalla porta (ARM/DISARM, TAKEOFF, DO_REPOSITION, LOITER_UNLIM, RTL, LAND, IMAGE_START_CAPTURE) e non invia mai movimento grezzo.

## 🔌 Interfacce

Il comando `hydra-umc-bridge-uav run` (o `python -m hydra_umc_bridge_uav run`) apre il collegamento MAVLink e mantiene il battito della stazione a terra. Codici di uscita: `0` arresto normale, `1` il collegamento non si è aperto o nessun battito è stato inviato, `2` opzione errata.

| Variabile | Predefinito | Cosa imposta |
|---|---|---|
| `--connection` | obbligatorio | un URL pymavlink, per esempio `udpin:0.0.0.0:14550` o `/dev/ttyUSB0` |
| `--heartbeat-hz` | `1.0` | da 0,1 a 10; il valore predefinito viene da `HYDRA_UAV_GCS_HEARTBEAT_HZ` |
| `--heartbeat-timeout` | `10.0` | secondi di attesa del primo battito del veicolo |
| `--status-every`, `--duration` | `10.0`, `0` | secondi tra le righe di stato (`0` non ne stampa); fermarsi dopo questi secondi (`0` gira fino all'arresto) |
| `--signing-key-file`, `--signing-allow-unsigned` | disattivato | file della chiave di firma MAVLink 2; accetta messaggi non firmati da un veicolo che non firma ancora |
| `--server-config` | disattivato | il `data/server-config.json` del Server: la sua sezione `mavlink` (impostata da Server Admin) dà il file della chiave e l'interruttore dei messaggi non firmati quando non li indicano né l'opzione né l'ambiente |

## ⚙️ Configurazione

| Variabile | Predefinito | Cosa imposta |
|---|---|---|
| `HYDRA_UAV_GCS_HEARTBEAT_HZ` | `1.0` | frequenza del battito di stazione a terra del bridge, da 0,1 a 10; `0` lo spegne |
| `HYDRA_UMC_BRIDGE_UAV_SIGNING_KEY_FILE` | non impostato | percorso della chiave di firma MAVLink 2 (32 byte, grezza o 64 caratteri esadecimali); prevale `--signing-key-file` |
| `HYDRA_UMC_BRIDGE_UAV_SIGNING_ALLOW_UNSIGNED` | non impostato | `1` accetta messaggi non firmati con una chiave; prevale `--signing-allow-unsigned`, poi questo, poi la configurazione del Server |
| `HYDRA_UMC_BRIDGE_UAV_SERVER_CONFIG` | non impostato | percorso del `data/server-config.json` del Server (prevale `--server-config`) |

## 🚀 Avvio rapido

Richiede Python 3.11 o successivo. I test e gli script di build si aspettano `HYDRA-UMC-SDK` clonato accanto a questo repository (`../HYDRA-UMC-SDK`) o indicato con `HYDRA_UMC_SDK_ROOT`.

Provalo senza hardware (questo verifica tutto il bridge con i suoi emulatori e test double):

```bash
py -3 tools/build_test.py          # Windows
python3 tools/build_test.py         # Linux, macOS
```

Avviare il bridge (richiede l'extra `mavlink`: `pip install ".[mavlink]"`):

```bash
hydra-umc-bridge-uav run --connection udpin:0.0.0.0:14550 --signing-key-file ~/uav-signing.key
```

## 🧪 Test

139 casi `unittest` automatici si eseguono con `python tools/build_test.py` (che compila anche ogni modulo). I test non richiedono hardware, rete né pacchetti opzionali: quelli che richiederebbero `paho-mqtt`, `pyserial` o una libreria del produttore usano un sostituto o vengono saltati se il pacchetto manca.

| File di test | Test | Cosa copre |
|---|---|---|
| `test_coordinator.py`, `test_observed_state.py`, `test_simulated_uav.py` | - | passaggio da lavoro a richiesta di volo, lo stato osservato del veicolo, quale richiesta è legale da quale stato |
| `test_mavlink_transport.py`, `test_mavlink_emulator.py`, `mavlink_emulator.py` | - | veri comandi MAVLink contro test double e un emulatore, riferimenti di quota, firma |
| `test_heartbeat.py`, `test_gcs_heartbeat.py`, `test_cli.py`, `test_edge_cases.py` | - | il failsafe di perdita collegamento, il battito della stazione a terra, la riga di comando e i suoi codici di uscita, casi limite |

La CI esegue `HYDRA_UMC_CI=1 python tools/ci_validate.py`, che controlla il manifesto, i documenti, gli otto README e le immagini, ed esegue i test con una copia dell'SDK.

## 🏗️ Compilazione e rilascio

Richiede Python 3.11+. `tools/build_test.py` si aspetta che `HYDRA-UMC-SDK` sia clonato come directory fratella (`../HYDRA-UMC-SDK`) o indicato tramite la variabile d'ambiente `HYDRA_UMC_SDK_ROOT`.

```bash
# Windows
build-test.bat      # solo validazione — nessun cambio di versione/CHANGELOG
build.bat            # valida e, se ha successo, aggiorna versione + CHANGELOG

# Linux/macOS
bash build-test.sh
bash build.sh
```

`build-test` compila ogni modulo sotto `src/` con `py_compile` ed esegue l'intera suite `unittest` (tutti i file `test_*.py` in `tests/`) - in modo deterministico, senza connessione reale a un UAV, senza rete e senza cambio di versione/CHANGELOG. `build` esegue prima quella stessa validazione e, solo in caso di successo, chiama `tools/bump_version.py` per sincronizzare la versione tra `pyproject.toml`, `hydra-umc.project.json` e `CHANGELOG.md`. Non esiste ancora un comando `run` con hardware reale - serve un adattatore di trasporto MAVLink/OSDK validato e un UAV reale.

## 🩺 Risoluzione dei problemi

| Vedi | Perché | Fai |
|---|---|---|
| `ModuleNotFoundError: hydra_umc_sdk` | L'SDK non è installato né accanto a questo repository | Clona `HYDRA-UMC-SDK` accanto a questo repository o imposta `HYDRA_UMC_SDK_ROOT` |
| exit code `1` | Il collegamento MAVLink non si è aperto o nessun battito è stato inviato | Controlla `--connection` (URL o dispositivo seriale) e che `pymavlink` sia installato (`.[mavlink]`) |
| exit code `2` | Un'opzione errata, per esempio una frequenza fuori da 0,1-10 o una chiave di firma illeggibile | Correggi l'opzione; il file della chiave deve contenere esattamente 32 byte (grezzi o 64 caratteri esadecimali) |
| `"allowed": false` + `reason` | La porta condivisa o una guardia ha rifiutato l'ordine | Leggi `reason` e correggi la causa; non riprovare alla cieca |

## 📂 Struttura del repository

<p align="center">
  <img src="images/repository-structure.svg" alt="Repository structure of HYDRA-UMC-BRIDGE-UAV" width="100%">
</p>

<details>
<summary>Versione testuale dell'albero</summary>

```text
HYDRA-UMC-BRIDGE-UAV/
├── src/hydra_umc_bridge_uav/
│   ├── coordinator.py             lavoro verso una richiesta di volo con nome
│   ├── mavlink_transport.py       richieste approvate come veri comandi MAVLink
│   ├── heartbeat.py               macchina a stati per la perdita del collegamento
│   ├── gcs_heartbeat.py           l'HEARTBEAT di stazione a terra del bridge
│   ├── cli.py                     `hydra-umc-bridge-uav run`
│   └── simulated_uav.py           quale richiesta è legale da quale stato
├── tests/                         139 test, con un emulatore MAVLink
├── docs/                          guida del bridge
├── tools/                         esecutore di build e test, aumento di versione, validatore CI, generatori e controlli di README e immagini
├── images/                        banner e immagini del README
├── .github/                       workflow CI e modelli di segnalazione
├── pyproject.toml                 metadati del pacchetto e dipendenze
├── hydra-umc.project.json         manifesto: versione, maturità, famiglia
├── CHANGELOG.md                   cosa è cambiato in ogni versione
├── build.sh build.bat             script di build (non cambiano mai la versione)
├── build-test.sh build-test.bat   script di build (non cambiano mai la versione)
├── test.sh test.bat               eseguono i test
└── SECURITY.md SUPPORT.md CONTRIBUTING.md CODE_OF_CONDUCT.md LICENSE LICENSE.md
```

</details>

## 🗺️ Roadmap e limiti noti

**Limiti noti oggi**

**Reale oggi:** versione `0.1.5`, funzionale come nucleo di coordinamento privo di dipendenze (`UavCoordinator`) più un watchdog reale di heartbeat per la perdita di collegamento completamente testato sui casi limite (`HeartbeatMonitor`), instradamento delle fasi chiuso, uno schema di richieste di volo statico `plan-only`, un trasporto reale dei comandi MAVLink (`MavlinkFlightControl`) che mappa ogni richiesta sul suo `MAV_CMD` reale e numerato - l'armamento ora richiede il booleano letterale `confirm_arm is True` (mai un valore semplicemente veritiero come la stringa `'false'`), ogni parametro numerico di volo viene verificato come numero reale non booleano prima di qualsiasi controllo di finitezza/intervallo, e un comando inviato viene confermato solo quando arriva il suo vero `COMMAND_ACK` con `MAV_RESULT_ACCEPTED` -, e script build-test non mutanti collegati alla CI con un checkout dell'SDK.

**Confine di integrazione:** questo ponte è solo un confine di coordinamento - non è un nodo di controllo di volo, e non può aggirare HYDRA-UMC-SERVER, i limiti dell'MCU, i watchdog o l'E-STOP; ogni lavoro inviato passa comunque attraverso la stessa porta condivisa usata da tutti i ponti fratelli. Il segnale di failsafe proprio di `HeartbeatMonitor` è una questione del livello di coordinamento, mai un sostituto del failsafe indipendente proprio del controller di volo.

**Ancora da fare:** nessun trasporto reale MAVLink (Pixhawk/PX4) o DJI OSDK, né un UAV fisico, è ancora stato validato - un adattatore reale sarà introdotto solo dopo che un controller di volo/SDK specifico sarà selezionato e testato.

## 🌍 Lingue

Questo README e i documenti del repository esistono in otto lingue: inglese, spagnolo, tedesco, francese, italiano, giapponese, cinese e portoghese (Portogallo). Sono scritti insieme e la CI verifica che gli otto file abbiano le stesse sezioni e gli stessi link. Questo repository non ha uno schermo né un catalogo di messaggi propri, quindi non ci sono stringhe di interfaccia da tradurre.

## 🔗 Progetti correlati

Questo progetto fa parte dell'ecosistema robotico HYDRA-UMC dello stesso autore (JuanenRac / Electro Hobby 3D). Vale la pena conoscerlo, poiché una richiesta potrebbe in realtà riguardare uno di questi invece di questo repository.

**Progetto Padre**
- **[HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER)** — il vero backend headless (REST/WebSocket) con cui parla davvero ogni client di controllo; il confine autenticato dell'ecosistema a cui questo bridge riporta una volta che ogni comando ha superato la barriera di sicurezza locale di questo stesso bridge.

**Progetti Fratelli** — parlano anch'essi con la stessa API di HYDRA-UMC-SERVER, ciascuno come proprio client
- **[HYDRA-UMC-STUDIO](https://github.com/JuanenRac/HYDRA-UMC-STUDIO)** — dashboard di controllo web con visualizzazione 3D multi-robot in tempo reale.
- **[HYDRA-UMC-SUITE](https://github.com/JuanenRac/HYDRA-UMC-SUITE)** — centro di comando sciame desktop (PySide6) per più server contemporaneamente, pacchettizzato come eseguibile standalone.
- **[HYDRA-UMC-ANDROID-CONTROL](https://github.com/JuanenRac/HYDRA-UMC-ANDROID-CONTROL)** — app di controllo nativa per Android con login biometrico e un companion Wear OS abbinato.
- **[HYDRA-UMC-IOS-CONTROL](https://github.com/JuanenRac/HYDRA-UMC-IOS-CONTROL)** — app di controllo per iOS/iPadOS (Flutter) con sincronizzazione WebSocket in tempo reale.
- **[HYDRA-UMC-DSI](https://github.com/JuanenRac/HYDRA-UMC-DSI)** — interfaccia touch nativa per il touchscreen DSI da 7" a bordo, incorporata direttamente nel CM5.
- **[HYDRA-UMC-BRIDGE-AMR](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-AMR)** — barriera di coordinamento per flotte AGV/AMR tramite un publisher MQTT VDA 5050 (testato solo contro una flotta emulata) — uno dei 3 bridge per flotte mobili dell'ecosistema.
- **[HYDRA-UMC-BRIDGE-CNC](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-CNC)** — coordinatore ad alto livello per celle CNC con accesso a stato/byte di controllo GRBL (testato solo contro un emulatore).
- **[HYDRA-UMC-BRIDGE-DROIDS](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-DROIDS)** — barriera di coordinamento per droidi con zampe/umanoidi, con un mittente di comandi per Boston Dynamics Spot (testato solo contro un emulatore) — uno dei 3 bridge per flotte mobili dell'ecosistema.
- **[HYDRA-UMC-BRIDGE-LASER](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-LASER)** — coordinatore per celle laser che legge 3 segnali GPIO di chiave/involucro/interblocco (testato solo contro un chip simulato; non può armare né far partire un laser).
- **[HYDRA-UMC-BRIDGE-OPENPNP](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-OPENPNP)** — coordinatore ad alto livello sicuro per il flusso schede del pick-and-place OpenPnP.
- **[HYDRA-UMC-BRIDGE-PRINTER3D](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-PRINTER3D)** — barriera di coordinamento sicura per stampanti 3D Moonraker/Klipper, con comandi di lavoro reali e controllati.
- **[HYDRA-UMC-BRIDGE-ROS2](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-ROS2)** — coordinatore con un trasporto ROS 2 rclpy importato in modo lazy (non ancora eseguito contro un'installazione ROS 2).

**Direttamente Correlati**
- **[HYDRA-UMC-SDK](https://github.com/JuanenRac/HYDRA-UMC-SDK)** — il contratto JSON-Schema condiviso e la barriera software di controllo dei job contro cui ogni bridge valida i propri comandi (non è una funzione di sicurezza certificata).
- **[HYDRA-UMC-CONNECTOR-HUB](https://github.com/JuanenRac/HYDRA-UMC-CONNECTOR-HUB)** — registro dichiarativo e validatore di manifesti adattatore per connettori di macchine esterne; estende la propria idea di contratto dell'SDK alle macchine esterne senza sostituire i progetti di gateway industriale.

**Fa Anche Parte dell'Ecosistema**

*Hardware e Piattaforma di Base*
- **[HYDRA-UMC](https://github.com/JuanenRac/HYDRA-UMC)** — il progetto della scheda madre del braccio robotico: host CM5 + coprocessore STM32H745 dual-core, pensato per coordinare fino a 8 bracci utensile via CAN-OTA/SPI-OTA (firmware in fase iniziale; nessuna scheda assemblata per ora).
- **[HYDRA-UMC-OS](https://github.com/JuanenRac/HYDRA-UMC-OS)** — livello prodotto riproducibile su Raspberry Pi OS per il CM5: agente in sola lettura, config/profili validati, provisioning WiFi al primo contatto.

*Backend Centrale e Client*
- **[HYDRA-UMC-EDITOR-URDF](https://github.com/JuanenRac/HYDRA-UMC-EDITOR-URDF)** — creatore/editor grafico desktop di URDF che invia i modelli finiti al catalogo di STUDIO.
- **[HYDRA-UMC-EDITOR-STL](https://github.com/JuanenRac/HYDRA-UMC-EDITOR-STL)** — editor desktop di modelli STL, che trasforma/sostituisce/rimuove/aggiunge veri pezzi nello stesso catalogo di modelli modificato anche da HYDRA-UMC-EDITOR-URDF.

*Piattaforma Strumenti URTC*
- **[URTC](https://github.com/JuanenRac/URTC)** — firmware per la scheda fisica dell'Universal Robot Tool Controller, oltre 25 profili utensile su bus CAN.
- **[URTC-FLASHER](https://github.com/JuanenRac/URTC-FLASHER)** — strumento desktop con GUI per il flashing delle schede URTC, CAN-OTA più SWD/JTAG a chip intero.
- **[URTC-TESTER](https://github.com/JuanenRac/URTC-TESTER)** — strumento desktop di diagnostica CAN-bus dal vivo per schede URTC, un pannello per profilo utensile.
- **[URTC-WEB-STUDIO](https://github.com/JuanenRac/URTC-WEB-STUDIO)** — alternativa basata su browser a URTC-TESTER tramite la Web Serial API, senza installazione locale.
- **[URTC-GATEWAY](https://github.com/JuanenRac/URTC-GATEWAY)** — gateway davanti al bus CAN di URTC: elenco di identificatori consentiti, limite di frequenza e chiave di sessione; software provato su computer, non ancora su un bus reale.

*Nodo IA Visione (Hailo-8)*
- **[HYDRA-UMC-VISION-NODE](https://github.com/JuanenRac/HYDRA-UMC-VISION-NODE)** — hub di integrazione per la pipeline di visione Hailo-8, con un vero controllo di prontezza hardware per fase.
- **[HYDRA-UMC-DETECTION-HEF](https://github.com/JuanenRac/HYDRA-UMC-DETECTION-HEF)** — registro reale di modelli compilati con verifica di caricamento sicuro per architettura Hailo/checksum.
- **[HYDRA-UMC-VISION-STREAMER](https://github.com/JuanenRac/HYDRA-UMC-VISION-STREAMER)** — generatore reale di pipeline GStreamer + config MediaMTX, con una vera barriera di integrazione HailoRT.
- **[HYDRA-UMC-VISUAL-SERVOING-API](https://github.com/JuanenRac/HYDRA-UMC-VISUAL-SERVOING-API)** — legge di correzione Position-Based Visual Servoing con autorizzazione condizionata allo stato di zona; ancora nessuna stima di posa da telecamera.
- **[HYDRA-UMC-SAFETY-ZONES](https://github.com/JuanenRac/HYDRA-UMC-SAFETY-ZONES)** — logica v0 di violazione zona, freschezza di calibrazione e richiesta E-STOP che legge le posizioni degli oggetti da un file; nessun rilevatore la alimenta e nessun E-STOP fisico è ancora collegato.

*Nodo IA Cognitivo (Hailo-10)*
- **[HYDRA-UMC-COGNITIVE-NODE](https://github.com/JuanenRac/HYDRA-UMC-COGNITIVE-NODE)** — hub di integrazione per la pipeline cognitiva Hailo-10 (orchestrazione LLM/VLA/voce).
- **[HYDRA-UMC-VLA-ENGINE](https://github.com/JuanenRac/HYDRA-UMC-VLA-ENGINE)** — vera codifica/decodifica di token d'azione e generazione di traiettoria per un modello Vision-Language-Action.
- **[HYDRA-UMC-VOICE-UI](https://github.com/JuanenRac/HYDRA-UMC-VOICE-UI)** — vero front-end vocale (VAD + parser di intenti) con un relay verso Watch limitato e soggetto a conferma.
- **[HYDRA-UMC-SEMANTIC-PLANNER](https://github.com/JuanenRac/HYDRA-UMC-SEMANTIC-PLANNER)** — vera scomposizione dei task basata su regole e recupero semantico degli errori sui codici errore MCU.
- **[HYDRA-UMC-DOCS-QA](https://github.com/JuanenRac/HYDRA-UMC-DOCS-QA)** — vera ricerca documentale TF-IDF (solo libreria standard) sui documenti Markdown di questo ecosistema.
- **[HYDRA-UMC-LOCAL-TECHNICIAN](https://github.com/JuanenRac/HYDRA-UMC-LOCAL-TECHNICIAN)** — tecnico di manutenzione IA locale e con cancelli di policy per l'ecosistema stesso - osserva, diagnostica e propone correzioni; i due livelli di rischio più alti non sono deliberatamente ancora implementati.

*Orchestrazione e Sciame*
- **[HYDRA-UMC-ORCHESTRATOR](https://github.com/JuanenRac/HYDRA-UMC-ORCHESTRATOR)** — hub di integrazione con un vero contratto di health-report gRPC/Protobuf e una macchina a stati di missione.
- **[HYDRA-UMC-JOB-DISPATCHER](https://github.com/JuanenRac/HYDRA-UMC-JOB-DISPATCHER)** — vera coda di lavori basata su priorità con deduplicazione, su una vera API HTTP.
- **[HYDRA-UMC-NODE-HEALING](https://github.com/JuanenRac/HYDRA-UMC-NODE-HEALING)** — vero watchdog di salute della flotta basato su gRPC, con retry/backoff e rilevamento di discrepanza d'identità.
- **[HYDRA-UMC-PATH-PLANNER-3D](https://github.com/JuanenRac/HYDRA-UMC-PATH-PLANNER-3D)** — vero pianificatore di percorsi 3D basato su RRT, con vera validazione delle collisioni ostacolo/spazio di lavoro.
- **[HYDRA-UMC-SWARM-SYNC](https://github.com/JuanenRac/HYDRA-UMC-SWARM-SYNC)** — vera sincronizzazione di stato CRDT LWW-Element-Map, con property test per la convergenza multi-cella.

*Gemello Digitale e Simulazione*
- **[HYDRA-UMC-TWIN](https://github.com/JuanenRac/HYDRA-UMC-TWIN)** — hub di integrazione per il motore di gemello digitale, con un vero contratto di sincronizzazione per compatibilità di versione.
- **[HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE)** — router hardware-in-the-loop con un interblocco software di collisione tra simulazione e hardware reale (testato senza hardware reale).
- **[HYDRA-UMC-PHYSICS-REPLICA](https://github.com/JuanenRac/HYDRA-UMC-PHYSICS-REPLICA)** — vera cinematica diretta e validazione dei limiti articolari su un vero sottoinsieme URDF.
- **[HYDRA-UMC-SYNTHETIC-DATA-GEN](https://github.com/JuanenRac/HYDRA-UMC-SYNTHETIC-DATA-GEN)** — vero generatore procedurale di scene 2D con esportazione di annotazioni YOLO/COCO.

*Dati e Analisi*
- **[HYDRA-UMC-DATALAKE](https://github.com/JuanenRac/HYDRA-UMC-DATALAKE)** — vero archivio di serie temporali basato su sqlite3, con una vera API HTTP di ingestione/query.
- **[HYDRA-UMC-ANOMALY-DETECTOR](https://github.com/JuanenRac/HYDRA-UMC-ANOMALY-DETECTOR)** — vero rilevatore di anomalie FFT + baseline statistica, con monitoraggio della deriva.
- **[HYDRA-UMC-PRODUCTION-REPORTS](https://github.com/JuanenRac/HYDRA-UMC-PRODUCTION-REPORTS)** — vero calcolo OEE/disponibilità sullo storico di DATALAKE, con esportazione CSV riproducibile.
- **[HYDRA-UMC-TELEMETRY-COLLECTOR](https://github.com/JuanenRac/HYDRA-UMC-TELEMETRY-COLLECTOR)** — vera pipeline di ingestione CAN/WebSocket verso DATALAKE, con deduplicazione per sequenza.

*Gateway Industriale*
- **[HYDRA-UMC-GATEWAY-INDUSTRIAL](https://github.com/JuanenRac/HYDRA-UMC-GATEWAY-INDUSTRIAL)** — hub di integrazione che inoltra ai protocolli industriali, con un vero livello di allowlist dei comandi/backpressure.
- **[HYDRA-UMC-OPCUA-SERVER](https://github.com/JuanenRac/HYDRA-UMC-OPCUA-SERVER)** — vero spazio di indirizzi OPC-UA, verificato con una vera sessione client del protocollo binario.
- **[HYDRA-UMC-MQTT-BROKER](https://github.com/JuanenRac/HYDRA-UMC-MQTT-BROKER)** — vero broker MQTT con autenticazione opzionale per client e ACL sui topic.
- **[HYDRA-UMC-MTCONNECT-ADAPTER](https://github.com/JuanenRac/HYDRA-UMC-MTCONNECT-ADAPTER)** — veri endpoint XML `/probe` e `/current` di MTConnect, con output in modalità degradata.

*Strumenti Complementari e Operazioni dell'Ecosistema*
- **[HYDRA-UMC-DASHBOARD-AI](https://github.com/JuanenRac/HYDRA-UMC-DASHBOARD-AI)** — pannelli Smart Summaries e Anomaly Highlighting su DATALAKE/ANOMALY-DETECTOR, con un fallback statistico onesto.
- **[HYDRA-UMC-TOOL-CLI](https://github.com/JuanenRac/HYDRA-UMC-TOOL-CLI)** — CLI di flotta con un vero e stabile contratto di exit-code, un client live reale della stessa API di HYDRA-UMC-SERVER.
- **[HYDRA-UMC-WATCH](https://github.com/JuanenRac/HYDRA-UMC-WATCH)** — app companion WearOS con schemi di avviso aptico e un relay vocale verso il telefono abbinato (non ancora eseguita su un orologio fisico).
- **[URTC-SMART-RACK](https://github.com/JuanenRac/URTC-SMART-RACK)** — firmware per un rack di montaggio schede con decodifica reale dell'ID utensile e logica di preriscaldamento Smart Idle.
- **[URTC-VISION-TOOL](https://github.com/JuanenRac/URTC-VISION-TOOL)** — firmware più un vero companion di visione Python per una testa utensile di ispezione termica/RGB.
- **[HYDRA-UMC-UPDATER](https://github.com/JuanenRac/HYDRA-UMC-UPDATER)** — strumento amministrativo desktop che scopre, clona e aggiorna ogni repository di questo ecosistema.
- **[HYDRA-UMC-OS-REBUILDER](https://github.com/JuanenRac/HYDRA-UMC-OS-REBUILDER)** — strumento desktop Windows/Linux che costruisce un'immagine della CM5 pronta da scrivere, precaricata con le versioni più aggiornate dell'ecosistema, con configurazione di primo avvio Wi-Fi/utente/SSH in stile Raspberry Pi Imager.
- **[HYDRA-UMC-OPS-AGENT](https://github.com/JuanenRac/HYDRA-UMC-OPS-AGENT)** — coordinatore di incidenti di manutenzione: un ruolo edge a basso privilegio raccoglie uno snapshot di inventario/salute sanificato, un ruolo control-plane lo rende in sola lettura e chiede a un provider di IA di suggerire una diagnosi - non applica mai una patch né distribuisce nulla.
- **[HYDRA-UMC-DEV-SERVER](https://github.com/JuanenRac/HYDRA-UMC-DEV-SERVER)** — host di sviluppo riproducibile (Raspberry Pi 5 / CM5) che conserva il codice sorgente dell'ecosistema ed esegue attività delimitate di build/test tramite una coda durevole; un ruolo di sviluppo dedicato, esplicitamente distinto da un CM5 operativo.

## 📚 Documentazione

Dove leggere di più:

* [docs/BRIDGE_GUIDE.md](docs/BRIDGE_GUIDE.md) - ambito, richieste di volo, MAVLink, firma, il failsafe, script
* [Base di sicurezza dell'ecosistema](https://github.com/JuanenRac/HYDRA-UMC-DOCS/blob/main/docs/SECURITY_BASELINE.md)
* [Modello delle minacce dell'ecosistema](https://github.com/JuanenRac/HYDRA-UMC-DOCS/blob/main/docs/THREAT_MODEL.md)
* [Registro delle modifiche di questo repository](CHANGELOG.md)
* [Licenza di questo repository](LICENSE.md)

## 🤝 Contribuire e comunità

* Prima di una pull request esegui `HYDRA_UMC_CI=1 py -3 tools/ci_validate.py` (oppure `HYDRA_UMC_CI=1 python3 tools/ci_validate.py`): deve stampare `CI_VALIDATION=PASS`. Non eseguire `build.sh` né `build.bat` per questa verifica; compilare non è verificare.
* I otto README sono scritti da un'unica fonte in HYDRA-UMC-DOCS; non modificarli a mano. Cambia la fonte e riscrivili con `tools/make_readmes.py`.
* Non affermare in un README ciò che non è stato eseguito: scrivi «simulato» o «non provato su hardware» quando è la verità.
* Vedi [CONTRIBUTING.md](CONTRIBUTING.md), il [codice di condotta](CODE_OF_CONDUCT.md), [SECURITY.md](SECURITY.md) e [SUPPORT.md](SUPPORT.md).

## 👤 AUTORE

**JuanenRac (Electro Hobby 3D)** · electrohobby3d@gmail.com

## 📜 LICENZA

GPL-3.0-or-later - [LICENSE](LICENSE).
