# Factorio Peninsula & Island Finder 🏝️⚙️

An automated scanner for Factorio map seeds that identifies defensible **peninsulas**, **choke points**, and **isolated islands** around the player spawn.

---

## 🔍 How It Works

1. **Headless Generation**: Invokes the Factorio engine in headless mode to render high-resolution map previews (`--generate-map-preview`) around spawn `(0, 0)`.
2. **Color Channel Analysis**: Identifies water and land tiles across the preview based on RGB channel signatures.
3. **Spawn Landmass BFS**: Performs an optimized breadth-first flood fill starting at the player's spawn point to trace the entire connected starting landmass.
4. **Perimeter Arc Calculation**: Scans the outer boundary of the preview area to calculate the contiguous circular arc of the border that does *not* connect to the starting landmass.
5. **Categorization & Logging**:
   - **`ISLAND` (100% Free Border)**: The spawn landmass is completely surrounded by water within the map preview bounds.
   - **`POSSIBLE ISLAND` (95.0% – 99.9% Free Border)**: Spawn landmass connects to the wider map only via a minuscule sliver / narrow isthmus.
   - **`PENINSULA` (50.0% – 94.9% Free Border)**: Spawn is protected on most sides by water, leaving a natural choke point for base defense.
   - Matching seed preview images and a log file (`found_seeds.txt`) are automatically saved to the output directory.

---

## 🚀 Quick Start with Docker

The container is designed as a **composable CLI tool**: it packages the official Factorio headless binary, the Python runtime, dependencies via [`uv`](https://github.com/astral-sh/uv), and accepts standard CLI arguments directly.

### Option A: Using Docker Compose (Recommended)

1. **Start the scanner** (uses settings in [compose.yaml](file:///Users/michael/Documents/Development/factoriopeninsulafinder/compose.yaml)):
   ```bash
   docker compose up --build
   ```

2. **Run a custom scan on-demand**:
   ```bash
   docker compose run --rm seed-finder --start-seed 500000 --count 200 --size 1024
   ```

3. **Check results**: Found seeds and preview images are saved locally to `./seed_previews/`.

---

### Option B: Using `docker run` directly

1. **Build the Docker image**:
   ```bash
   docker build -t factorio-peninsula-finder .
   ```

2. **Run the container**:
   ```bash
   docker run --rm -it \
     --platform linux/amd64 \
     -v "$(pwd)/seed_previews:/app/seed_previews" \
     factorio-peninsula-finder \
     --start-seed 1000000 \
     --count 500 \
     --size 2048 \
     --preset default
   ```

> **Note for Apple Silicon (macOS) users**: The Factorio headless binary is `linux/amd64`. Docker Desktop automatically handles emulation via `--platform linux/amd64` (already configured in `compose.yaml`).

---

## 💻 Running Locally (without Docker)

If you have Factorio installed locally and [`uv`](https://docs.astral.sh/uv/) or Python 3.10+:

```bash
# Run with uv (automatically resolves dependencies: Pillow, tqdm)
uv run find_peninsula.py \
  --factorio-bin "/path/to/factorio" \
  --start-seed 1000 \
  --count 100 \
  --size 1024
```

---

## ⚙️ CLI Options & Configuration

| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--start-seed` | `int` | `1000` | Starting world seed number to scan. |
| `--count` | `int` | `100` | Total number of sequential seeds to test. |
| `--size` | `int` | `1024` | Resolution (width and height in px) of generated map previews. |
| `--min-ratio` | `float` | `0.50` | Minimum free border ratio (`0.0` to `1.0`) to consider a peninsula. |
| `--preset` | `str` | `default` | Map gen preset (`default`, `rail-world`, `death-world`, `rich-resources`, etc.). |
| `--out-dir` | `str` | `./seed_previews` | Directory to save match preview images and logs. |
| `--out-file` | `str` | `found_seeds.txt` | Text filename where found seeds are appended. |
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

## 🛠️ Debug Mode (`--debug`)

- **Default behavior**: Annotated overlay images (`DEBUG_seed_<seed>.png`) are generated automatically for all **saved / matched** seeds. Non-matching seeds are discarded to save disk space and maximize throughput.
- **`--debug` flag**: When `--debug` is enabled, annotated images are generated for **all** scanned seeds (including non-matches).

---

## 📜 License

MIT License. Factorio is a registered trademark of Wube Software LLC.
