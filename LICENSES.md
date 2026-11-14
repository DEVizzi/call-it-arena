# Asset & Data Licenses (`LICENSES.md`)

All fonts, flag icons, audio tracks, sound effects, and external datasets used by **Call It Arena** (`DEVizzi/call-it-arena`) are 100% free for commercial and monetized use on YouTube Shorts and Instagram Reels.

---

## 1. Fonts (`/assets/fonts/`)

| File | Font Family | Author / Foundry | License | Source |
| :--- | :--- | :--- | :--- | :--- |
| `BebasNeue-Regular.ttf` | Bebas Neue | Ryoichi Tsunekawa (Dharma Type) | **SIL Open Font License 1.1 (OFL-1.1)** | [Google Fonts - Bebas Neue](https://fonts.google.com/specimen/Bebas+Neue) |
| `Inter-Bold.ttf` | Inter | Rasmus Andersson | **SIL Open Font License 1.1 (OFL-1.1)** | [Google Fonts - Inter](https://fonts.google.com/specimen/Inter) |
| `Inter-Regular.ttf` | Inter | Rasmus Andersson | **SIL Open Font License 1.1 (OFL-1.1)** | [Google Fonts - Inter](https://fonts.google.com/specimen/Inter) |

---

## 2. Country Flag Icons (`/assets/flags/`)

| Files | Package | Author | License | Source |
| :--- | :--- | :--- | :--- | :--- |
| `in.png`, `us.png`, `br.png`, `id.png`, `ph.png`, `pk.png`, `bd.png`, `ng.png`, `mx.png`, `gb.png`, `de.png`, `jp.png`, `tr.png`, `eg.png`, `vn.png`, `ca.png` (+ extras) | `flag-icons` | Panayiotis Lipiridis (`lipis`) | **MIT License** | [github.com/lipis/flag-icons](https://github.com/lipis/flag-icons) |

---

## 3. Background Music (`/assets/music/`)

| File | Description | Generator / Author | License |
| :--- | :--- | :--- | :--- |
| `arena_pulse_cc0.wav` | 128 BPM electronic arena synth loop | Procedurally synthesized in-repo (`common/audio.py`) | **CC0 1.0 Universal (Public Domain Dedication)** |
| `quiz_tension_cc0.wav` | 120 BPM rhythmic quiz tension pulse | Procedurally synthesized in-repo (`common/audio.py`) | **CC0 1.0 Universal (Public Domain Dedication)** |

---

## 4. Sound Effects (`/assets/sfx/`)

| File | Description | Generator / Author | License |
| :--- | :--- | :--- | :--- |
| `boing.wav` | Physics marble collision bounce chime | Procedurally synthesized in-repo (`common/audio.py`) | **CC0 1.0 Universal (Public Domain Dedication)** |
| `tick.wav` | Crisp UI / sorting comparison tick | Procedurally synthesized in-repo (`common/audio.py`) | **CC0 1.0 Universal (Public Domain Dedication)** |
| `countdown_beep.wav` | 3-2-1 lock-in countdown tone | Procedurally synthesized in-repo (`common/audio.py`) | **CC0 1.0 Universal (Public Domain Dedication)** |
| `reveal_fanfare.wav` | Winner reveal chord fanfare | Procedurally synthesized in-repo (`common/audio.py`) | **CC0 1.0 Universal (Public Domain Dedication)** |

---

## 5. External Data & Media APIs

| Format | Source API | License & Compliance Enforcement |
| :--- | :--- | :--- |
| **Format 3: Data Guess** | World Bank Open Data API (`api.worldbank.org`) / Our World in Data | **CC BY 4.0** — Source attribution burned into chart footer (`Source: World Bank / OWID (CC BY 4.0)`). |
| **Format 4: Space Guess** | NASA Astronomy Picture of the Day (`api.nasa.gov/planetary/apod`) | **Public Domain (17 U.S.C. § 105)** — `generators/space_guess.py` strictly skips any entry where `media_type == "video"` OR a `copyright` field exists in the API response. |
| **Format 6: Guess the Year** | Wikimedia REST API (`On this day`) + Wikimedia Commons API (`imageinfo` `extmetadata`) | **Public Domain / CC0 / CC BY / CC BY-SA** — `generators/guess_the_year.py` queries `iiprop=extmetadata` and rejects any image without an explicit free license (`Public domain`, `CC0`, `CC BY`, `CC BY-SA`), burning credit/license into the video footer. |
| **Voiceover (TTS)** | Piper TTS (`en_US-lessac-medium`) | **MIT License** (Piper engine) / **CC BY 4.0** (Lessac dataset). |
