---
name: Raspberry Pi5
description: Infos et astuces sur le Raspberry Pi 5
triggers: ["raspberry", "pi5", "Pi5", "raspberry pi", "raspberry pi5"]
created: 2026-07-19
---

## Raspberry Pi 5 – Fiche technique complète

- **Processeur** : Broadcom BCM2712, quad‑core Cortex‑A76 @ 2,4 GHz (ARM v8).  
- **Mémoire RAM** : 4 Go, 8 Go ou 16 Go LPDDR4X (IA embarquée, serveur, etc.).
- **GPU** : VideoCore VII, support OpenGL ES 3.2, décodage matériel 4 K @ 60 fps (H.264/H.265).  
- **Stockage interne** : Slot micro‑SD UHS‑I.  
- **Extension de stockage** : Port USB‑C 5 V 3 A compatible avec des boîtiers SSD NVMe. Un SSD NVMe de **256 Go** à *1 To** (en Hat) est un point de départ recommandé, mais le même port accepte des SSD de 512 Go, ou plus de 1 To, offrant des performances de lecture/écriture supérieures à 2000 Mo/s.
- **Connectivité** : Wi‑Fi 6 (802.11ax), Bluetooth 5.3, Gigabit Ethernet, 2× USB‑3.0, 2× USB‑2.0, HDMI 2.1 (4 K @ 60 Hz), GPIO 40‑pins, CSI/DSI pour caméra et écran.
- **Alimentation** : Connecteur USB‑C, 5 V 3 A (chargeur USB‑C moderne).
- **Système d’exploitation** : Raspberry Pi OS 64‑bits (recommandé), Ubuntu, LibreELEC, RetroPie, etc.

### Points forts par rapport au Pi 4
1. **Performance CPU** : ~2× plus rapide grâce à l’architecture Cortex‑A76.  
2. **GPU** : support natif du 4 K, décodage matériel HEVC.  
3. **Mémoire** : option 16 Go pour les charges lourdes.  
4. **Stockage SSD NVMe** : latence très faible, idéal pour bases de données ou serveurs de médias.  
5. **Port USB‑C** réversible et plus robuste.

### Cas d’usage typiques
- **Serveur domestique** (Pi‑Hole, Home‑Assistant, Nextcloud) avec SSD NVMe pour des temps d’accès quasi‑instantanés.  
- **Media‑center 4 K** (Kodi, Plex) grâce au décodage matériel.  
- **Projets d’IA embarquée** (Ollama, TensorFlow Lite) profitant de la RAM 16 Go et du GPU.  
- **Robotique / IoT** (GPIO, caméras CSI, capteurs) avec capacité de traitement accrue.

### Astuces rapides pour bien démarrer
1. **Flash** la dernière image Raspberry Pi OS 64‑bits sur une carte micro‑SD (minimum 16 Go, classe 10).  
2. **Activer** le SSH et le Wi‑Fi via `raspi-config` dès le premier boot.  
3. **Mettre à jour** le firmware (`sudo apt update && sudo apt full-upgrade`).  
4. **Brancher** un SSD NVMe en HAT M2 ou en boîtier externe, l’utiliser comme disque principal et prévoir des Back-Up sur clé USB et/ou carte SD.

Cette fiche peut être consultée à chaque fois que vous avez besoin d’un rappel rapide des spécifications ou des bonnes pratiques de mise en route du Raspberry Pi 5.
