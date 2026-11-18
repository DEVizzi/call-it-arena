# Call It Arena (`DEVizzi/call-it-arena`)

> **"Call it before the reveal. 👇"**
> Zero-touch, 100% free Python + GitHub Actions engine that generates and publishes **2 interactive vertical videos per day** (`1080x1920`, `30fps`, `20–40s`) to **YouTube Shorts** and **Instagram Reels**.

---

## 1. Architecture & Interactive Formats

Every video follows the high-retention **Call It Arena** structure:
`Hook (0–2s)` → `Interactive Simulation / Progressive Clues` → `3-2-1 Countdown ("Lock in your guess!")` → `Final ~5s Reveal` → `Outro ("Comment your pick for the next one 👇")`, with **burned-in captions**, **Piper TTS voiceover**, **synced collision/tick SFX**, and **CC0 background music at 15% volume**.

| Format | Share | Generator | Description |
| :--- | :---: | :--- | :--- |
| **1. Country Marble League** *(Flagship)* | ~50% (Slot B) | `generators/marble_league.py` + `generators/marble_arenas.py` | 16-country headless `Pygame` + `Pymunk` physics race across **14 arenas** — escape arenas (`single_ring`, `double_ring`, `spiral_labyrinth`, `funnel_obstacles`, `triple_ring`, `hexagon_spin`, `square_crusher`, `gear_ring`, `gravity_well`) and drop races to a finish line (`plinko_drop`, `zigzag_ramps`, `windmill_drop`, `hourglass_rush`, `pendulum_gauntlet`). An arena is never reused within 9 races, and physics pacing is auto-tuned per race. **Country names stay hidden during the race** (anonymous `ESCAPED 3/12` counter) and are revealed at the end: race result table (position, time, points) → season standings so far + India spotlight + cliffhanger. Tracks a 4-week season in `state/league.json`. |
| **2. Flag Quiz** | ~10% (Slot A) | `generators/flag_quiz.py` | Heavily pixelated/blurred flag (`Pillow`) that sharpens across 5 stages with 2 geographic clues, 4 options (`A–D`), countdown, and a fun fact reveal. |
| **3. World Data Bar Race** | ~10% (Slot A) | `generators/data_guess.py` | Animated horizontal bar chart race (`Matplotlib` `Agg` piped to `FFmpeg`) using World Bank / Our World in Data metrics. Hides the #1 country name until the reveal. |
| **4. Deep Space Mystery** | ~10% (Slot A) | `generators/space_guess.py` | NASA APOD API slow `zoompan` reveal (`"Galaxy, Nebula, Planet, or Star Cluster?"`). Strictly skips `media_type == "video"` and any entry with a `copyright` field. |
| **5. Algorithm Sorting Race** | ~10% (Slot A) | `generators/sorting_race.py` | Side-by-side split-screen race of two sorting algorithms (`QuickSort`, `MergeSort`, `HeapSort`, `ShellSort`) on identical scrambled arrays with pitched synthesizer ticks. |
| **6. Guess The Year** | ~10% (Slot A) | `generators/guess_the_year.py` | Wikipedia `"On this day"` API + license-checked Wikimedia Commons image (`CC0`, `Public domain`, `CC BY`, `CC BY-SA`), masking the year (`[ ???? ]`) until the reveal. |
| **7. Plinko Bucket Drop** | ~10% (Slot A) | `generators/bucket_drop.py` | A golden ball falls down a tall scrolling `Pymunk` Plinko tower (pegs, bumpers, spinners) into 7 numbered buckets. Lands in slow motion right after the countdown. |
| **8. Tower Defence Season** | ~10% (Slot A) | `generators/tower_quake.py` | All 6 rigid-body tower designs (Needle, Temple, Zigzag, Twin Towers, Spire, Fortress) face a different escalating disaster every game — Earthquake, Ground Slam, Tilting Ground, Meteor Shower, Wrecking Ball, Hurricane, Cannon Barrage, Bowling Strike, Bomb Blitz — until one tower stands last. |
| **9. Downhill Derby** | ~10% (Slot A) | `generators/downhill_derby.py` | Four engine-less jointed physics cars (5 designs) roll down a procedural bumpy hill with jumps; scrolling camera + mini track map. |

### Seasons, finales & champions

| Game | Structure | Finale & winner showcase |
| :--- | :--- | :--- |
| Country Marble League | 28-race season: Group Stage (1–14) → Knockouts (15–21, last place eliminated) → Championship Finals (22–27, **2x points**) → **Grand Final race 28** | Grand Final = 40s **MEGA RACE** on the multi-stage `mega_gauntlet` (ring → windmills → hourglass) at **3x points**, then a **Season Champion** ceremony (trophy, podium, final table) before Season N+1 starts. Arena types still change every day. |
| Downhill Derby | 10-race Derby Series, all 5 cars every race (points 10/6/4/2/1) | Race 10 = finale at 3x; standings card after every race, **Champion Car** card after the finale. |
| Tower Defence | 10-game season: the 9 disasters shuffled (each once), survival points 10/7/5/3/2/1 | Game 10 = **APOCALYPSE** finale (quake + meteors + hurricane) at 3x → **Tower Defence Champion**. Standings + "next disaster" teaser after every game. |
| Plinko Bucket Drop | 10-drop "Hot Bucket" series | Drop 10 = finale (3x) → **Hottest Bucket** of the series. |

Series state lives in `state/series.json`; league state in `state/league.json`. Neither is ever written by dry runs or the preview workflow.

All physics formats share `common/physics_kit.py` (declarative arena geometry, kinematic drivers, time remapping so the decisive moment lands just after the 3-2-1 reveal) and `common/physics_video.py` (TTS + audio + HUD + FFmpeg).

> [!TIP]
> Safe local testing during a live season: `python main.py --format marble_league --arena plinko_drop --dry-run` — dry runs never write `state/league.json` or `state/posted.json`.

---

## 2. GitHub Secrets Configuration

Navigate to **`https://github.com/DEVizzi/call-it-arena/settings/secrets/actions`** and configure the following repository secrets. Our config loader is backward-compatible with your secret names from `DEVizzi/quotegram`:

| Secret Name | Legacy Alias Supported (`quotegram`) | Required? | Notes |
| :--- | :--- | :---: | :--- |
| `IG_USER_ID` | `IG_BUSINESS_ACCOUNT_ID` | **Yes** | Your Instagram Business/Creator Account ID (connected to `__silentink__` / `@callitarena`). |
| `IG_ACCESS_TOKEN` | `FB_PAGE_ACCESS_TOKEN` | **Yes** | Long-lived Instagram Graph API / Page Access Token with `instagram_basic`, `instagram_content_publish`, `instagram_manage_comments`, `instagram_manage_insights`. |
| `YT_CLIENT_ID` | `YOUTUBE_CLIENT_ID` | **Yes** | Google Cloud OAuth 2.0 Client ID. |
| `YT_CLIENT_SECRET` | `YOUTUBE_CLIENT_SECRET` | **Yes** | Google Cloud OAuth 2.0 Client Secret. |
| `YT_REFRESH_TOKEN` | `YOUTUBE_REFRESH_TOKEN` | **Yes** | **Action Needed**: Refresh your expired token using the command below (`python -m common.upload_youtube --reauth`). |
| `GEMINI_API_KEY` | `GOOGLE_API_KEY` | **Yes** | Google AI Studio free-tier Gemini API key (used for SEO title scoring & comment parsing; falls back cleanly to templates if omitted). |
| `NASA_API_KEY` | *(NEW secret)* | **Yes** | **NEW**: Generate a free instant API key at [https://api.nasa.gov](https://api.nasa.gov) for Format 4 (Space Guess). |

### How to Generate a Fresh `YT_REFRESH_TOKEN` (Since Old Token Expired)

Run this one-time command locally:

```bash
export YT_CLIENT_ID="your-google-oauth-client-id"
export YT_CLIENT_SECRET="your-google-oauth-client-secret"
python -m common.upload_youtube --reauth
```

A browser window will open to authorize your YouTube channel with `youtube.upload`, `youtube`, and `youtube.force-ssl` scopes, and print the new `YT_REFRESH_TOKEN` to paste into GitHub Secrets.

---

## 3. One-Time Google Cloud Step: YouTube API Compliance Audit

> [!IMPORTANT]
> **Why this matters:** Unverified Google Cloud projects created after July 28, 2020 may have their uploaded videos locked to `private` (or hit restricted quota limits) until the project completes Google's free **YouTube API Services Audit**.

1. Open the [Google Cloud Console](https://console.cloud.google.com/) for your YouTube project.
2. Go to **APIs & Services → OAuth consent screen** and set **Publishing status** to **In production** (if it is still in "Testing", refresh tokens expire every 7 days!).
3. Submit the free [YouTube API Services — Audit and Quota Extension Form](https://support.google.com/youtube/contact/yt_api_form):
   - **Project Usage**: Internal first-party publishing automation for your own channel (`@callitarena`, 2 Shorts/day).
   - **API Quota Needed**: Default `10,000` units/day is plenty (`2 uploads × 1,600 = 3,200 units/day`).

---

## 4. Changing the Schedule or Format Weights (`schedule.yaml`)

All timing and format distribution weights live in [`schedule.yaml`](schedule.yaml) so you never need to touch Python code to rebalance:

- **Slot A (`02:07 UTC` = `07:37 IST`)**: Catches US evening prime time (`~9 PM ET / 6 PM PT`) + India morning commute. Runs a weighted choice from Formats 2–9.
- **Slot B (`14:07 UTC` = `19:37 IST`)**: Catches India prime time + Europe afternoon. Runs **Country Marble League** (and **Weekly Standings + Mega Quiz** on Sundays).
- **Automated Sunday Analytics Rebalancer**: Every Sunday, `common/analytics.py` writes `reports/weekly-YYYY-MM-DD.md` and automatically reduces the weight of any format averaging `< 50%` of channel views for 3 weeks in a row, while boosting formats `> 2x` average (capped at `70%` max share).

---

## 5. How to Add a New Format (`Format 7+`)

1. Create `generators/my_new_format.py` exposing `generate_my_new_format_video(seed: str, output_dir: Path, ...) -> Dict[str, Any]`.
2. Use `FFmpegFramePipe` and `apply_standard_hud` from `common/render.py` plus `mix_master_audio` from `common/audio.py` so the video matches the `1080x1920`, `30fps`, `20–40s` brand template.
3. Return a metadata dict containing `"video_path"`, `"winner"`, `"duration_sec"`, `"reveal_timestamp_sec"`, and `"comment_question"`.
4. Register the format name in `main.py` (`ROTATING_GUESS_FORMATS` and `invoke_generator`), `common/seo.py`, and add its weight and playlist name to `schedule.yaml`.

---

## 6. Manual Rebrand Checklist (`Silent Ink` → `Call It Arena`)

*(No old posts are deleted via API. Complete these steps manually in YouTube Studio & the Instagram App before enabling live cron runs.)*

- [ ] **1. Rename Both Accounts**:
  - **YouTube Studio** (`Customization → Basic info`):
    - **Name**: `Call It Arena`
    - **Handle**: `@callitarena` *(if taken at the moment you save, use `@callitarena.official`, `@thecallitarena`, or `@playcallitarena`)*
  - **Instagram App** (`Edit profile` on `__silentink__`):
    - **Name**: `Call It Arena | Daily Battles & Quizzes`
    - **Username**: `callitarena`
- [ ] **2. Update Bio & Cross-Links**:
  - **Bio / Description**:
    ```text
    Call it before the reveal. 👇
    🏆 Country Marble League (Daily 7:30 PM IST)
    🎯 Flag, Space, Data & History Challenges
    💬 Comment your country to enter the next race!
    ```
  - **Links**: Link your YouTube channel (`youtube.com/@callitarena`) in your Instagram bio, and link `instagram.com/callitarena` in YouTube Studio (`Basic info → Links`).
- [ ] **3. Upload Generated Brand Art (`/branding`)**:
  - Upload [`branding/profile_picture.png`](branding/profile_picture.png) (`800x800`) to both YouTube and Instagram.
  - Upload [`branding/youtube_banner.png`](branding/youtube_banner.png) (`2560x1440`) as your YouTube Channel Banner.
- [ ] **4. Archive / Remove Old "Silent Ink" Posts**:
  - **Instagram**: Go to `Your activity → Content you shared → Reels/Posts`, select old Silent Ink quote/poem posts, and tap **Archive** (recommended over permanent deletion so historical account trust remains intact).
  - **YouTube Studio**: Go to `Content → Shorts`, select old Silent Ink videos, and set **Visibility → Unlisted / Private** (or delete if preferred).
- [ ] **5. Create Series Playlists on YouTube**:
  - Our uploader (`common/upload_youtube.py`) automatically creates these playlists if missing, or you can pre-create them in YouTube Studio:
    - `Marble League Season 1`
    - `Guess The Flag Quiz`
    - `World Data Bar Race`
    - `Deep Space Mystery (NASA)`
    - `Algorithm Sorting Race`
    - `Guess The Year History Quiz`
    - `Plinko Bucket Drop`
    - `Tower Defence Season`
    - `Downhill Derby (Physics Cars)`
