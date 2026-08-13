#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "Pillow",
#     "numpy",
#     "scipy",
#     "tqdm",
# ]
# ///

import argparse
import concurrent.futures
import os
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import sys
from datetime import datetime

import numpy as np
from scipy import ndimage
from PIL import Image, ImageDraw

from tqdm import tqdm
import tqdm.std as tqdm_std
import tqdm.utils as tqdm_utils

# =========================================================
# 1. Prevent the Semaphore Leak & Ensure Thread-Safe Locking
# =========================================================
tqdm.set_lock(threading.RLock())

# =========================================================
# 2. Force Custom Time Formatting (e.g., 5h3m10s)
# =========================================================
def custom_format_interval(t):
    t = int(t)
    h, m, s = t // 3600, (t % 3600) // 60, t % 60
    if h > 0:
        return f"{h}h{m}m{s}s"
    elif m > 0:
        return f"{m}m{s}s"
    else:
        return f"{s}s"

tqdm_std.format_interval = custom_format_interval
tqdm_utils.format_interval = custom_format_interval
# =========================================================

# Factorio's --map-gen-seed-max renders every *second* seed in the interval,
# so a contiguous range needs one pass per parity.
SEED_STRIDE = 2

PROGRESS_FILENAME = "scan_progress.txt"

# How often a running batch is scanned for newly-finished previews.
PREVIEW_POLL_SECONDS = 0.25


def get_cpu_count():
    """Returns available logical CPU cores/hyperthreads, respecting container affinity if set."""
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, NotImplementedError, OSError):
        return os.cpu_count() or 4


# Only three colours in a default-preset preview satisfy the blue-channel test
# (measured over 400 previews / 54M px): water (38,64,73) and (51,83,95), plus
# iron ore (105,133,147). Ore's channel margins are indistinguishable from
# shallow water's (b-r 42 vs 44, b-g 14 vs 12) -- brightness is what separates
# them, with 33 points of headroom either side of the cut.
WATER_MAX_BLUE = 128


def water_mask(rgb):
    """Boolean mask of water tiles, excluding blue-grey ore patches on land."""
    r = rgb[..., 0].astype(np.int16)
    g = rgb[..., 1].astype(np.int16)
    b = rgb[..., 2].astype(np.int16)
    return (b > r + 10) & (b > g + 5) & (b < WATER_MAX_BLUE)


def find_spawn_pixel(water, width, height):
    """Nearest non-water pixel to the image centre, searching outward as the original did."""
    cx, cy = width // 2, height // 2
    if not water[cy, cx]:
        return cx, cy, True

    for r_search in range(1, 100):
        offsets = [
            (r_search, 0), (-r_search, 0), (0, r_search), (0, -r_search),
            (r_search, r_search), (-r_search, -r_search), (r_search, -r_search), (-r_search, r_search)
        ]
        for dx, dy in offsets:
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < width and 0 <= ny < height and not water[ny, nx]:
                return nx, ny, True

    return cx, cy, False


def border_ring(mask):
    """Border pixels in clockwise order: top row, right column, bottom row, left column."""
    return np.concatenate([
        mask[0, :],
        mask[1:, -1],
        mask[-1, -2::-1],
        mask[-2:0:-1, 0],
    ])


def border_ring_coords(width, height):
    """(x, y) coordinates matching border_ring's ordering."""
    xs = np.concatenate([
        np.arange(width),
        np.full(height - 1, width - 1),
        np.arange(width - 2, -1, -1),
        np.zeros(height - 2, dtype=int),
    ])
    ys = np.concatenate([
        np.zeros(width, dtype=int),
        np.arange(1, height),
        np.full(width - 1, height - 1),
        np.arange(height - 2, 0, -1),
    ])
    return xs, ys


def longest_circular_run(flags):
    """Length and start index of the longest wrap-around run of True in `flags`."""
    n = flags.size
    if n == 0:
        return 0, -1
    if flags.all():
        return n, 0
    if not flags.any():
        # Spawn landmass reaches the whole border: no free arc at all.
        return 0, -1

    doubled = np.concatenate([flags, flags])
    padded = np.concatenate([[False], doubled, [False]])
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    starts, ends = edges[::2], edges[1::2]
    lengths = ends - starts

    best = int(lengths.argmax())
    return min(int(lengths[best]), n), int(starts[best])


def analyze_perimeter_peninsula(image_path):
    """Returns (free_border_ratio, save_debug_image) for a Factorio map preview."""
    img = Image.open(image_path)
    arr = np.asarray(img)
    height, width = arr.shape[:2]
    rgb = arr[..., :3]

    water = water_mask(rgb)
    cx, cy, start_found = find_spawn_pixel(water, width, height)

    if not start_found:
        def save_empty_debug(debug_path):
            annotated_img = img.convert("RGB")
            draw = ImageDraw.Draw(annotated_img)
            draw.ellipse([(cx - 5, cy - 5), (cx + 5, cy + 5)], fill=(255, 0, 0))
            annotated_img.save(debug_path)
        return 1.0, save_empty_debug

    labels, _ = ndimage.label(~water)
    visited = labels == labels[cy, cx]

    is_non_spawn_border = ~border_ring(visited)
    max_arc, arc_start_idx = longest_circular_run(is_non_spawn_border)
    free_border_ratio = max_arc / is_non_spawn_border.size

    def save_debug_image(debug_path):
        # Uniform 150/255 dark overlay, green where the spawn landmass was traced.
        base = rgb.astype(np.uint16)
        tint = np.zeros_like(base)
        tint[..., 1] = np.where(visited, 255, 0)
        blended = (base * 105 + tint * 150 + 127) // 255

        annotated_img = Image.fromarray(blended.astype(np.uint8), "RGB")
        draw = ImageDraw.Draw(annotated_img)

        if arc_start_idx != -1 and max_arc > 0:
            xs, ys = border_ring_coords(width, height)
            ring_len = xs.size
            for i in range(arc_start_idx, arc_start_idx + max_arc):
                px, py = int(xs[i % ring_len]), int(ys[i % ring_len])
                draw.rectangle([px - 2, py - 2, px + 2, py + 2], fill=(0, 100, 255))

        draw.ellipse([(cx - 5, cy - 5), (cx + 5, cy + 5)], fill=(255, 0, 0))
        annotated_img.save(debug_path)

    return free_border_ratio, save_debug_image


def get_timestamp():
    return f"[{datetime.now().strftime('%H:%M:%S')}]"


def classify(seed, preview_path, out_dir, min_ratio, debug):
    """Analyze one preview, save it if it matches, and return (seed, ratio, log_entry).

    Runs inside a worker process, so everything here must be picklable input/output only.
    """
    debug_file = os.path.join(out_dir, f"DEBUG_seed_{seed}.png")
    free_border_ratio, save_debug_func = analyze_perimeter_peninsula(preview_path)

    if free_border_ratio == 1.0:
        match_filename = os.path.join(out_dir, f"ISLAND_seed_{seed}.png")
        log_entry = f"{seed} - ISLAND (100% free border)\n"
        message = f"[ ISLAND ] Seed {seed}: 100% free border! True island detected -> Saved"
    elif 0.95 <= free_border_ratio < 1.0:
        match_filename = os.path.join(out_dir, f"POSSIBLE_ISLAND_seed_{seed}_{int(free_border_ratio*100)}pct.png")
        log_entry = f"{seed} - POSSIBLE ISLAND ({free_border_ratio:.1%} free border)\n"
        message = f"[ POSSIBLE ISLAND ] Seed {seed}: {free_border_ratio:.1%} free border! -> Saved"
    elif free_border_ratio >= min_ratio:
        match_filename = os.path.join(out_dir, f"PENINSULA_seed_{seed}_{int(free_border_ratio*100)}pct.png")
        log_entry = f"{seed} - PENINSULA ({free_border_ratio:.1%} free border)\n"
        message = f"[ MATCH ] Seed {seed}: {free_border_ratio:.1%} free border! -> Saved"
    else:
        if debug and save_debug_func:
            save_debug_func(debug_file)
        return seed, free_border_ratio, None, None

    if os.path.exists(match_filename):
        os.remove(match_filename)
    # The preview lives in a temp dir that is usually a different device to out_dir.
    shutil.move(preview_path, match_filename)
    if save_debug_func:
        save_debug_func(debug_file)

    return seed, free_border_ratio, log_entry, message


def png_is_complete(path):
    """True once the file ends with a PNG IEND chunk, i.e. Factorio has finished it.

    Previews are picked up while the engine is still writing the rest of the
    batch, so a half-written file must never be handed to the analyzer.
    """
    try:
        with open(path, "rb") as f:
            if f.seek(0, os.SEEK_END) < 12:
                return False
            # Final chunk is 4-byte length, b"IEND", 4-byte CRC.
            f.seek(-8, os.SEEK_END)
            return f.read(8)[:4] == b"IEND"
    except OSError:
        return False


def generate_previews(seed_start, seed_end, batch_dir, args, config_path,
                      procs_lock, active_procs, shutdown_event):
    """Yield (seed, path) for each preview as Factorio finishes writing it.

    One Factorio process per parity: --map-gen-seed-max walks the interval in
    steps of SEED_STRIDE, so a contiguous range needs SEED_STRIDE passes. Each
    process pays the ~1s prototype-load cost once for the whole batch.

    Previews are yielded during generation rather than after the process exits,
    so analysis overlaps rendering and the progress bar advances continuously.
    """
    os.makedirs(batch_dir, exist_ok=True)
    seen = set()

    def harvest():
        for name in os.listdir(batch_dir):
            if name in seen or not name.endswith(".png"):
                continue
            path = os.path.join(batch_dir, name)
            if not png_is_complete(path):
                continue
            seen.add(name)
            try:
                yield int(name[:-4]), path
            except ValueError:
                continue

    for offset in range(SEED_STRIDE):
        if shutdown_event.is_set():
            return

        first = seed_start + offset
        if first > seed_end:
            continue

        cmd = [
            args.factorio_bin,
            "-c", config_path,
            "--generate-map-preview", batch_dir + os.sep,
            "--map-gen-seed", str(first),
            "--map-gen-seed-max", str(seed_end),
            "--map-preview-size", str(args.size),
            "--preset", args.preset
        ]
        if args.map_gen_settings:
            cmd.extend(["--map-gen-settings", args.map_gen_settings])
        if args.map_settings:
            cmd.extend(["--map-settings", args.map_settings])

        # stderr goes to a file rather than a pipe: nothing drains a pipe while
        # we poll, and a full pipe buffer would deadlock the engine.
        err_path = os.path.join(batch_dir, f"stderr_{offset}.log")
        with open(err_path, "w+") as err_file:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err_file, text=True)
            with procs_lock:
                active_procs.add(proc)
            try:
                while proc.poll() is None:
                    yield from harvest()
                    if shutdown_event.is_set():
                        return
                    time.sleep(PREVIEW_POLL_SECONDS)
            finally:
                with procs_lock:
                    active_procs.discard(proc)

            yield from harvest()

            if shutdown_event.is_set():
                return

            err_file.seek(0)
            stderr_out = err_file.read()

        try:
            os.remove(err_path)
        except OSError:
            pass

        if proc.returncode != 0:
            err_line = stderr_out.strip().splitlines()[-1] if stderr_out and stderr_out.strip() else ""
            err_suffix = f": {err_line}" if err_line else ""
            tqdm.write(
                f"{get_timestamp()} Warning: Factorio exited with code {proc.returncode} "
                f"for seeds {first}-{seed_end}{err_suffix}",
                file=sys.stdout,
            )


def process_batch(seed_start, seed_end, args, temp_base_dir, analysis_pool,
                  seed_log_path, log_lock, procs_lock, active_procs, matches,
                  shutdown_event, worker_configs_queue, progress_path, pbar, pbar_lock):
    """Analyze one batch of previews as Factorio renders them."""
    if shutdown_event.is_set():
        return

    batch_dir = os.path.join(temp_base_dir, f"batch_{seed_start}")

    def harvest(futures, block):
        """Handle finished analyses; returns the futures still outstanding."""
        if block:
            done, still_pending = set(futures), set()
            concurrent.futures.wait(futures)
        else:
            done = {f for f in futures if f.done()}
            still_pending = [f for f in futures if f not in done]

        for future in done:
            try:
                seed, _ratio, log_entry, message = future.result()
            except Exception as e:
                tqdm.write(f"{get_timestamp()} Error analyzing a seed in batch {seed_start}: {e}", file=sys.stdout)
                with pbar_lock:
                    pbar.update(1)
                continue

            if log_entry:
                tqdm.write(f"{get_timestamp()} {message}", file=sys.stdout)
                with log_lock:
                    matches.append(seed)
                    with open(seed_log_path, "a") as f:
                        f.write(log_entry)
            with pbar_lock:
                pbar.update(1)

        return list(still_pending)

    pending = []
    submitted = 0
    try:
        config_path = worker_configs_queue.get()
        try:
            for seed, path in generate_previews(seed_start, seed_end, batch_dir, args, config_path,
                                                procs_lock, active_procs, shutdown_event):
                submitted += 1
                pending.append(
                    analysis_pool.submit(classify, seed, path, args.out_dir, args.min_ratio, args.debug)
                )
                # Non-blocking, so rendering is never held up by analysis.
                pending = harvest(pending, block=False)
        finally:
            worker_configs_queue.put(config_path)

        if shutdown_event.is_set():
            return

        harvest(pending, block=True)
        pending = []

        expected = seed_end - seed_start + 1
        if submitted < expected:
            # Never record a batch as done when previews went missing: resume
            # would skip seeds that were never actually scanned.
            tqdm.write(
                f"{get_timestamp()} Warning: batch {seed_start}-{seed_end} analyzed "
                f"{submitted} of {expected} previews; not recording it as complete.",
                file=sys.stdout,
            )
            with pbar_lock:
                pbar.update(expected - submitted)
            return

        with log_lock:
            with open(progress_path, "a") as f:
                f.write(f"{seed_start} {seed_end}\n")

    finally:
        for future in pending:
            future.cancel()
        shutil.rmtree(batch_dir, ignore_errors=True)


# Amortizing engine startup pulls batches larger; keeping every worker busy pulls
# them smaller. Auto-sizing targets one batch per worker, bounded so tiny scans
# still amortize and huge scans keep resume granularity usable.
MIN_AUTO_BATCH = 16
MAX_AUTO_BATCH = 512


def resolve_batch_size(requested, count, workers):
    if requested is not None:
        return max(1, requested)
    per_worker = count // max(workers, 1)
    return max(MIN_AUTO_BATCH, min(MAX_AUTO_BATCH, per_worker))


def load_completed_chunks(progress_path):
    """Chunk (start, end) pairs already finished by an earlier run."""
    completed = set()
    if not os.path.exists(progress_path):
        return completed
    with open(progress_path) as f:
        for line in f:
            parts = line.split()
            if len(parts) == 2:
                try:
                    completed.add((int(parts[0]), int(parts[1])))
                except ValueError:
                    continue
    return completed


def main():
    detected_cpus = get_cpu_count()
    parser = argparse.ArgumentParser(description="Factorio Peninsula & Island Finder (Multi-Core)")
    parser.add_argument("--factorio-bin", default="factorio", help="Path to Factorio executable")
    parser.add_argument("--start-seed", type=int, default=1000, help="Starting seed number")
    parser.add_argument("--count", type=int, default=100, help="Number of seeds to scan")
    parser.add_argument("-j", "--workers", type=int, default=detected_cpus,
                        help=f"Number of parallel worker threads (default: {detected_cpus}, all available logical CPU cores/hyperthreads)")
    parser.add_argument("--out-dir", default="./seed_previews", help="Directory to save matches")
    parser.add_argument("--out-file", default="found_seeds.txt", help="Filename to append found seeds")
    parser.add_argument("--size", type=int, default=1024, help="Image resolution (px)")
    parser.add_argument("--min-ratio", type=float, default=0.50, help="Minimum free border ratio to consider a peninsula (default: 0.50)")
    parser.add_argument("--preset", default="default", help="Map generation preset (e.g. default, rail-world, death-world)")
    parser.add_argument("--map-gen-settings", default=None, help="Optional path to map-gen-settings.json")
    parser.add_argument("--map-settings", default=None, help="Optional path to map-settings.json")
    parser.add_argument("--batch-size", type=int, default=None,
                        help="Seeds rendered per Factorio process (default: auto — sized so every worker gets a batch). "
                             "Larger amortizes engine startup further; too large and there are fewer batches than workers, "
                             "leaving cores idle.")
    parser.add_argument("--temp-dir", default=None,
                        help="Scratch directory for previews being analyzed (default: system temp). Keep this off a bind mount.")
    parser.add_argument("--no-resume", action="store_true",
                        help="Rescan chunks already recorded in scan_progress.txt instead of skipping them")
    parser.add_argument("--debug", action="store_true", help="Generate and save debug visualization overlays for all scanned seeds")

    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    seed_log_path = os.path.join(args.out_dir, args.out_file)
    progress_path = os.path.join(args.out_dir, PROGRESS_FILENAME)

    batch_size = resolve_batch_size(args.batch_size, args.count, args.workers)
    chunks = []
    for chunk_start in range(args.start_seed, args.start_seed + args.count, batch_size):
        chunk_end = min(chunk_start + batch_size, args.start_seed + args.count) - 1
        chunks.append((chunk_start, chunk_end))

    skipped_seeds = 0
    if not args.no_resume:
        completed = load_completed_chunks(progress_path)
        if completed:
            remaining = [c for c in chunks if c not in completed]
            skipped_seeds = sum(end - start + 1 for start, end in chunks if (start, end) in completed)
            if skipped_seeds:
                print(f"{get_timestamp()} Resuming: skipping {skipped_seeds} seeds already scanned "
                      f"({len(chunks) - len(remaining)} of {len(chunks)} batches complete).")
            chunks = remaining

    total_to_scan = sum(end - start + 1 for start, end in chunks)

    print(f"{get_timestamp()} Scanning {total_to_scan} seeds starting from {args.start_seed} using {args.workers} parallel workers "
          f"({len(chunks)} batches of up to {batch_size})...\n")

    if not chunks:
        print(f"{get_timestamp()} Nothing to do. Pass --no-resume to rescan.")
        return

    matches = []
    log_lock = threading.Lock()
    procs_lock = threading.Lock()
    pbar_lock = threading.Lock()
    active_procs = set()
    shutdown_event = threading.Event()

    def handle_sigint(_signum, _frame):
        # Kill Factorio immediately rather than waiting for the executor to drain;
        # batches are long-lived now, so relying on the with-block exit would hang.
        if shutdown_event.is_set():
            return
        shutdown_event.set()
        tqdm.write(f"\n{get_timestamp()} Scan interrupted by user. Terminating active workers...", file=sys.stdout)
        with procs_lock:
            for p in list(active_procs):
                try:
                    p.kill()
                except OSError:
                    pass

    signal.signal(signal.SIGINT, handle_sigint)

    # Progress bar with dynamic time display
    pbar = tqdm(
        total=total_to_scan,
        ascii=".o",
        bar_format="Factorio{bar} | {percentage:3.0f}% | {n_fmt}/{total_fmt} seeds | ETA: {remaining}",
        file=sys.stdout
    )

    stop_refresh = threading.Event()
    def background_refresh():
        while not stop_refresh.wait(1.0):
            pbar.refresh()

    refresher_thread = threading.Thread(target=background_refresh)
    refresher_thread.daemon = True
    refresher_thread.start()

    with tempfile.TemporaryDirectory(prefix="factorio_workers_", dir=args.temp_dir) as temp_base_dir:
        worker_configs_queue = queue.Queue()
        for i in range(args.workers):
            w_dir = os.path.join(temp_base_dir, f"worker_{i}")
            os.makedirs(w_dir, exist_ok=True)
            cfg_path = os.path.join(w_dir, "config.ini")
            with open(cfg_path, "w") as f:
                f.write(f"[path]\nread-data=__PATH__executable__/../../data\nwrite-data={w_dir}\n")
            worker_configs_queue.put(cfg_path)

        analysis_pool = concurrent.futures.ProcessPoolExecutor(max_workers=args.workers)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = [
                    executor.submit(
                        process_batch,
                        chunk_start,
                        chunk_end,
                        args,
                        temp_base_dir,
                        analysis_pool,
                        seed_log_path,
                        log_lock,
                        procs_lock,
                        active_procs,
                        matches,
                        shutdown_event,
                        worker_configs_queue,
                        progress_path,
                        pbar,
                        pbar_lock,
                    )
                    for chunk_start, chunk_end in chunks
                ]

                for future in concurrent.futures.as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        tqdm.write(f"{get_timestamp()} Batch failed: {e}", file=sys.stdout)

        finally:
            analysis_pool.shutdown(wait=not shutdown_event.is_set(), cancel_futures=shutdown_event.is_set())
            stop_refresh.set()
            if refresher_thread.is_alive():
                refresher_thread.join()
            pbar.close()
        # Leaving the with-block removes temp_base_dir, so no previews are orphaned.

    if shutdown_event.is_set():
        print(f"\n{get_timestamp()} Stopped after {len(matches)} matches. Completed batches are recorded in "
              f"{progress_path}; rerun the same command to resume.")
    else:
        print(f"\n{get_timestamp()} Scan complete. Found {len(matches)} matching seeds.")


if __name__ == "__main__":
    main()
