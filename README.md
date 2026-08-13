# Factorio Peninsula & Island Finder 🏝️⚙️

An automated, high-performance scanner for Factorio map seeds that identifies defensible **peninsulas**, **choke points**, and **isolated islands** around the player spawn.

---

## ⚡ Key Features

- **Batched Map Generation**: Renders hundreds of previews per Factorio process via `--map-gen-seed-max`, so the engine's ~1 s prototype-load cost is paid once per batch instead of once per seed.
- **Multi-Core & Hyperthreading Parallelization**: Automatically detects and leverages all physical CPU cores and logical hyperthreads (e.g., 8, 16, 32 threads). Map generation runs across concurrent Factorio processes; image analysis runs in a separate process pool, so neither stage is bottlenecked by Python's GIL.
- **Vectorized Analysis**: NumPy + SciPy connected-component labelling traces the spawn landmass in a single pass — roughly 40x faster than a per-pixel flood fill, with identical results.
- **Island Prescan**: Optionally screens seeds using Factorio's terrain noise directly — no preview rendering — and only renders island candidates. Measured 43x faster with an identical match set. See [Island Prescan](#-island-prescan---island-prescan).
- **Resumable Scans**: Completed batches are recorded, so an interrupted run picks up where it left off instead of restarting.
- **Intelligent Geography Classification**: Analyzes pixel channel data and labels the connected starting landmass.
- **Defensible Choke-Point Detection**: Measures the longest circular perimeter arc free of spawn land to detect natural defensive choke points.
- **Automatic Visual Overlays**: Saves both the clean Factorio map preview and an annotated overlay (`DEBUG_seed_<seed>.png`) for all matching seeds.
- **Instant Docker Deployment**: Pre-built container with embedded headless Factorio engine and pre-warmed dependencies.

---

## 🔍 How It Works

1. **Batched Headless Generation**: Invokes the Factorio engine in headless mode to render map previews (`--generate-map-preview`) around spawn `(0, 0)`. Each process renders a whole batch of seeds using `--map-gen-seed-max`, amortizing engine startup across the batch.
2. **Color Channel Analysis**: Identifies water and land tiles across the preview based on RGB channel signatures. (Blue-grey iron ore is explicitly excluded — its channel margins are nearly identical to shallow water's, so brightness is used to separate them.)
3. **Spawn Landmass Labelling**: Runs `scipy.ndimage.label` over the land mask and selects the component containing the player's spawn point.
4. **Perimeter Arc Calculation**: Scans the outer boundary of the preview area to calculate the contiguous circular arc of the border that does *not* connect to the starting landmass.
5. **Categorization & Logging**:
   - **`ISLAND` (100% Free Border)**: The spawn landmass is completely surrounded by water within the map preview bounds.
   - **`POSSIBLE ISLAND` (95.0% – 99.9% Free Border)**: Spawn landmass connects to the wider map only via a minuscule sliver / narrow isthmus.
   - **`PENINSULA` (50.0% – 94.9% Free Border)**: Spawn is protected on most sides by water, leaving a natural choke point for base defense.
   - Matching seed preview images and a log file (`found_seeds.txt`) are automatically saved to the output directory.

---

## 🚀 Quick Start with Docker

The container is designed as a **composable CLI tool**: it packages the official Factorio headless binary, the Python runtime, its dependencies (Pillow, NumPy, SciPy, tqdm), and accepts standard CLI arguments directly.

### Option A: Using Docker Compose (Recommended)

1. **Start the scanner** (uses settings in [compose.yaml](file:///Users/michael/Documents/Development/factoriopeninsulafinder/compose.yaml)):
   ```bash
   docker compose up --build
   ```

2. **Run a custom scan on-demand with custom workers**:
   ```bash
   # Automatically uses all available CPU threads (or override with -j <threads>)
   docker compose run --rm seed-finder --start-seed 500000 --count 1000 --size 1024 -j 16
   ```

3. **Check results**: Found seeds and preview images are saved locally to `./seed_previews/`.

---

### Option B: Using `docker run` directly

1. **Build the Docker image**:
   ```bash
   docker build -t factorio-peninsula-finder .
   ```

2. **Run the container (uses all CPU cores/threads by default)**:
   ```bash
   docker run --rm -it \
     --platform linux/amd64 \
     -v "$(pwd)/seed_previews:/app/seed_previews" \
     factorio-peninsula-finder \
     --start-seed 1000000 \
     --count 1000 \
     --size 2048 \
     --preset default \
     -j 16
   ```

> **Note for Apple Silicon (macOS) users**: The Factorio headless binary is `linux/amd64`. Docker Desktop automatically handles emulation via `--platform linux/amd64` (already configured in `compose.yaml`).

---

## 💻 Running Locally (without Docker)

If you have Factorio installed locally and [`uv`](https://docs.astral.sh/uv/) or Python 3.10+:

```bash
# Run with uv (automatically resolves dependencies: Pillow, NumPy, SciPy, tqdm)
uv run find_peninsula.py \
  --factorio-bin "/path/to/factorio" \
  --start-seed 1000 \
  --count 500 \
  --size 1024 \
  -j 8
```

---

## ⚙️ CLI Options & Configuration

| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--start-seed` | `int` | `1000` | Starting world seed number to scan. |
| `--count` | `int` | `100` | Total number of sequential seeds to test. |
| `-j`, `--workers` | `int` | *All logical cores* | Number of parallel worker threads / concurrent Factorio instances (takes full advantage of multi-core & hyperthreading). |
| `--size` | `int` | `1024` | Size of the search window around spawn, in **tiles** (1 px = 1 tile). This is *not* a quality setting — it sets how far from spawn the peninsula question is asked, and results at different `--size` values are not comparable. Cost scales with area, i.e. `size²`. |
| `--min-ratio` | `float` | `0.50` | Minimum free border ratio (`0.0` to `1.0`) to consider a peninsula. |
| `--preset` | `str` | `default` | Map gen preset (`default`, `rail-world`, `death-world`, `rich-resources`, etc.). |
| `--out-dir` | `str` | `./seed_previews` | Directory to save match preview images and logs. |
| `--out-file` | `str` | `found_seeds.txt` | Text filename where found seeds are appended. |
| `--batch-size` | `int` | *auto* | Seeds rendered per Factorio process. Auto-sized to give every worker a batch (bounded to 16–512). Larger values amortize engine startup further, but fewer batches than workers leaves cores idle. |
| `--temp-dir` | `str` | *system temp* | Scratch directory for previews being analyzed. Keep this off a bind mount — only matches are written to `--out-dir`. |
| `--no-resume` | `flag` | `False` | Rescan batches already recorded in `scan_progress.txt` instead of skipping them. |
| `--debug` | `flag` | `False` | Generate and save debug overlay images for all scanned seeds (saved seeds always generate debug images). |
| `--map-gen-settings` | `str` | `None` | Path to a custom `map-gen-settings.json` file. |
| `--map-settings` | `str` | `None` | Path to a custom `map-settings.json` file. |
| `--factorio-bin` | `str` | `factorio` | Path to the Factorio executable. |

---

## 📁 Output Structure

When a matching seed is discovered, both the clean map preview and the annotated debug visualization overlay are saved in `./seed_previews/`:

```
seed_previews/
├── found_seeds.txt                         # Appended list of all discovered seeds and match types
├── scan_progress.txt                       # Completed batch ranges, used to resume interrupted scans
├── ISLAND_seed_10101035.png                # Clean Factorio map preview
├── DEBUG_seed_10101035.png                 # Visual overlay showing landmass & perimeter arc
├── PENINSULA_seed_10101047_68pct.png       # 68% water perimeter peninsula
└── DEBUG_seed_10101047.png                 # Annotated overlay
```

### Visual Overlay Annotations:
- **Green Tint**: The flood-filled connected spawn landmass.
- **Blue Markers**: The longest detected contiguous perimeter arc free of spawn land.
- **Red Dot**: Spawn point origin `(0, 0)`.

### Example `found_seeds.txt`
```
10101035 - ISLAND (100.0% free border)
10101092 - POSSIBLE ISLAND (98.2% free border)
10101047 - PENINSULA (68.4% free border)
```

---

## 📐 Choosing `--size`

`--map-preview-size` renders at a fixed scale of **1 pixel per tile**, so `--size` selects the *world area* examined, not the level of detail. A `--size 2048` preview is the `--size 1024` preview with more surrounding terrain revealed — verified by cropping: the centre 1024×1024 of a 2048 preview is 99.96% identical to the 1024 preview of the same seed.

Two consequences:

- **Results are not comparable across sizes.** Seed `20271569 + 10` scores 67.5% (`PENINSULA`) at `--size 1024` but 100% (`ISLAND`) at `--size 2048`, because the wider window reveals that the landmass closes off. Pick a size that matches the base radius you care about and stay with it.
- **Cost scales with area.** Measured generation floor on an 18-core host, 504 seeds, all workers busy:

| `--size` | window | ms/seed | seeds/sec |
| :--- | :--- | ---: | ---: |
| 256 | 256×256 tiles | 8.8 | 113 |
| 512 | 512×512 tiles | 21.5 | 47 |
| 1024 | 1024×1024 tiles | 78.6 | 13 |
| 2048 | 2048×2048 tiles | 312 | 3.2 |

Because a smaller preview is a *crop* rather than a downscale, a low-resolution pre-screen is not possible — a seed that looks landlocked in a small window may still be a peninsula in a larger one. The one safe implication runs the other way: a seed classified `ISLAND` at a given size stays an island at every larger size, since its landmass is already fully enclosed.

---

## 🏝️ Island Prescan (`--island-prescan`)

Islands are *rare* — a 100,000-seed scan can turn up none at all. Rendering a full preview for every seed just to reject it is the wrong shape of work, so this mode skips rendering entirely for the seeds it can rule out.

Factorio will evaluate terrain elevation at arbitrary sparse points from Lua, without generating chunks. The prescan casts 64 rays from spawn to the edge of the `--size` window. An island's landmass is bounded, so **every** ray must cross water to leave it — meaning a single ray that reaches the edge on dry land is proof the seed is *not* an island. Survivors are rendered at full resolution and classified by the normal analyzer, so results are exact.

```bash
docker run --rm -it --platform linux/amd64 --tmpfs /tmp \
  -v "$(pwd)/seed_previews:/app/seed_previews" \
  factorio-peninsula-finder \
  --island-prescan --start-seed 1000000 --count 1000000 --size 2048 --min-ratio 0.95
```

Measured over 2,000 seeds at `--size 2048`:

| | exhaustive | `--island-prescan` |
| :--- | ---: | ---: |
| wall clock | 599 s | **14 s** |
| per seed | 300 ms | **7 ms** |
| seeds rendered | 2,000 | **4 (0.20%)** |
| islands found | 1 | **1** (identical match set) |

### It only works for islands

The screen proves a seed is *not* an island. It cannot rank partial peninsulas: one still connects to the window edge across a wide arc, so plenty of rays run clear. On 498 seeds with ground truth, correlation between clear-ray count and the true free-border ratio was just **-0.287**, and seeds scoring ≥ 0.50 had up to 12 clear rays — a cutoff loose enough to keep them retains 56% of all seeds, which buys nothing.

So `--island-prescan` **requires `--min-ratio >= 0.95`** and errors out otherwise. For ordinary peninsula hunting, use the normal path.

| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--island-prescan` | `flag` | `False` | Enable the prescan. Requires `--min-ratio >= 0.95`. |
| `--prescan-workers` | `int` | `8` | Headless servers used for screening. 8 is the measured optimum; throughput *falls* above it. |
| `--prescan-max-clear` | `int` | `0` | Keep seeds with at most this many clear rays. `0` is exact for true islands; raise to admit near-islands at higher cost. |
| `--prescan-slice` | `int` | `50000` | Seeds screened per pass. Bounds work lost to an interrupt. |
| `--prescan-port-base` | `int` | `34197` | First UDP port; one per prescan worker. |

---

## ⏸️ Resuming an Interrupted Scan

Each completed batch appends its seed range to `scan_progress.txt` in the output directory. Re-running the **same command** skips those batches and continues where it stopped:

```bash
docker compose run --rm seed-finder --start-seed 1000000 --count 1000000
# Ctrl-C at any point, then re-run the identical command to resume.
```

Resume granularity is one batch, so at most one partial batch per worker is repeated. Pass `--no-resume` to force a full rescan, or delete `scan_progress.txt`.

> Progress is keyed on the batch's seed range, so changing `--start-seed`, `--count`, or `--batch-size` between runs redefines the batch boundaries and previously recorded batches will no longer match.

---

## 🛠️ Debug Mode (`--debug`)

- **Default behavior**: Annotated overlay images (`DEBUG_seed_<seed>.png`) are generated automatically for all **saved / matched** seeds. Non-matching seeds are discarded to save disk space and maximize throughput.
- **`--debug` flag**: When `--debug` is enabled, annotated images are generated for **all** scanned seeds (including non-matches).

---

## 📜 License

MIT License. Factorio is a registered trademark of Wube Software LLC.
