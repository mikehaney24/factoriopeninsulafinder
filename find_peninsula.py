#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "Pillow",
#     "tqdm",
# ]
# ///

import argparse
import os
import subprocess
import threading
import sys
from collections import deque
from datetime import datetime
from PIL import Image, ImageDraw

from tqdm import tqdm
import tqdm.std as tqdm_std
import tqdm.utils as tqdm_utils

# =========================================================
# 1. Prevent the Semaphore Leak
# =========================================================
tqdm.set_lock(threading.RLock())

# =========================================================
# 2. Force Custom Time Formatting (e.g., 5h3m10s)
# Patching both modules guarantees tqdm uses it.
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

def analyze_perimeter_peninsula(image_path):
    img = Image.open(image_path).convert("RGB")
    width, height = img.size
    pixels = img.load()
    
    def is_water(x, y):
        r, g, b = pixels[x, y]
        return b > r + 10 and b > g + 5

    cx, cy = width // 2, height // 2
    start_found = False
    
    if not is_water(cx, cy):
        start_found = True
    else:
        for r_search in range(1, 100):
            offsets = [
                (r_search, 0), (-r_search, 0), (0, r_search), (0, -r_search),
                (r_search, r_search), (-r_search, -r_search), (r_search, -r_search), (-r_search, r_search)
            ]
            for dx, dy in offsets:
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < width and 0 <= ny < height and not is_water(nx, ny):
                    cx, cy = nx, ny
                    start_found = True
                    break
            if start_found:
                break

    if not start_found:
        def save_empty_debug(debug_path):
            annotated_img = img.copy()
            draw = ImageDraw.Draw(annotated_img)
            draw.ellipse([(cx-5, cy-5), (cx+5, cy+5)], fill=(255, 0, 0))
            annotated_img.save(debug_path)
        return 1.0, save_empty_debug

    visited = bytearray(width * height)
    visited[cy * width + cx] = 1
    
    queue = deque([(cx, cy)])
    directions = [(-1, 0), (1, 0), (0, -1), (0, 1)]
    
    while queue:
        px, py = queue.popleft()
        for dx, dy in directions:
            nx, ny = px + dx, py + dy
            if 0 <= nx < width and 0 <= ny < height:
                idx = ny * width + nx
                if not visited[idx] and not is_water(nx, ny):
                    visited[idx] = 1
                    queue.append((nx, ny))

    perimeter_pixels = []
    for x in range(width): perimeter_pixels.append((x, 0))
    for y in range(1, height): perimeter_pixels.append((width - 1, y))
    for x in range(width - 2, -1, -1): perimeter_pixels.append((x, height - 1))
    for y in range(height - 2, 0, -1): perimeter_pixels.append((0, y))

    is_non_spawn_border = [visited[py * width + px] == 0 for px, py in perimeter_pixels]
    doubled_border = is_non_spawn_border + is_non_spawn_border
    
    max_arc = 0
    current_arc = 0
    for is_non_spawn in doubled_border:
        if is_non_spawn:
            current_arc += 1
            max_arc = max(max_arc, current_arc)
        else:
            current_arc = 0
            
    max_arc = min(max_arc, len(perimeter_pixels))
    free_border_ratio = max_arc / len(perimeter_pixels)

    def save_debug_image(debug_path):
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 150))
        overlay_pixels = overlay.load()
        
        for y in range(height):
            row_idx = y * width
            for x in range(width):
                if visited[row_idx + x]:
                    overlay_pixels[x, y] = (0, 255, 0, 150)
                    
        annotated_img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(annotated_img)
        
        arc_start_idx = "".join(['1' if b else '0' for b in doubled_border]).find('1' * max_arc)
        if arc_start_idx != -1:
            for i in range(arc_start_idx, arc_start_idx + max_arc):
                px, py = perimeter_pixels[i % len(perimeter_pixels)]
                draw.rectangle([px-2, py-2, px+2, py+2], fill=(0, 100, 255))
                
        draw.ellipse([(cx-5, cy-5), (cx+5, cy+5)], fill=(255, 0, 0))
        annotated_img.save(debug_path)

    return free_border_ratio, save_debug_image

def get_timestamp():
    return f"[{datetime.now().strftime('%H:%M:%S')}]"

def main():
    parser = argparse.ArgumentParser(description="Factorio Peninsula & Island Finder")
    parser.add_argument("--factorio-bin", default="factorio", help="Path to Factorio executable")
    parser.add_argument("--start-seed", type=int, default=1000, help="Starting seed number")
    parser.add_argument("--count", type=int, default=100, help="Number of seeds to scan")
    parser.add_argument("--out-dir", default="./seed_previews", help="Directory to save matches")
    parser.add_argument("--out-file", default="found_seeds.txt", help="Filename to append found seeds")
    parser.add_argument("--size", type=int, default=1024, help="Image resolution (px)")
    parser.add_argument("--min-ratio", type=float, default=0.50, help="Minimum free border ratio to consider a peninsula (default: 0.50)")
    parser.add_argument("--preset", default="default", help="Map generation preset (e.g. default, rail-world, death-world)")
    parser.add_argument("--map-gen-settings", default=None, help="Optional path to map-gen-settings.json")
    parser.add_argument("--map-settings", default=None, help="Optional path to map-settings.json")
    parser.add_argument("--debug", action="store_true", help="Generate and save debug visualization overlays for all scanned seeds")
    
    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    
    seed_log_path = os.path.join(args.out_dir, args.out_file)
    
    print(f"{get_timestamp()} Scanning {args.count} seeds starting from {args.start_seed}...\n")
    
    matches = []
    
    # This renders "Factorio" statically, and expands the '.' and 'o' dynamically
    pbar = tqdm(
        total=args.count, 
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

    try:
        for i in range(args.count):
            seed = args.start_seed + i
            temp_file = os.path.join(args.out_dir, f"temp_preview_{seed}.png")
            debug_file = os.path.join(args.out_dir, f"DEBUG_seed_{seed}.png")
            
            cmd = [
                args.factorio_bin,
                "--generate-map-preview", temp_file,
                "--map-gen-seed", str(seed),
                "--map-preview-size", str(args.size),
                "--preset", args.preset
            ]
            if args.map_gen_settings:
                cmd.extend(["--map-gen-settings", args.map_gen_settings])
            if args.map_settings:
                cmd.extend(["--map-settings", args.map_settings])
            
            proc = None
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                proc.wait()
                if proc.returncode != 0:
                    tqdm.write(f"{get_timestamp()} Warning: Factorio exited with code {proc.returncode} for seed {seed}", file=sys.stdout)
                    continue
                    
                free_border_ratio, save_debug_func = analyze_perimeter_peninsula(temp_file)
                match_found = False
                log_entry = ""
                
                if free_border_ratio == 1.0:
                    match_filename = os.path.join(args.out_dir, f"ISLAND_seed_{seed}.png")
                    os.rename(temp_file, match_filename)
                    if save_debug_func:
                        save_debug_func(debug_file)
                    tqdm.write(f"{get_timestamp()} [ ISLAND ] Seed {seed}: 100% free border! True island detected -> Saved", file=sys.stdout)
                    matches.append(seed)
                    match_found = True
                    log_entry = f"{seed} - ISLAND (100% free border)\n"
                    
                elif 0.95 <= free_border_ratio < 1.0:
                    match_filename = os.path.join(args.out_dir, f"POSSIBLE_ISLAND_seed_{seed}_{int(free_border_ratio*100)}pct.png")
                    os.rename(temp_file, match_filename)
                    if save_debug_func:
                        save_debug_func(debug_file)
                    tqdm.write(f"{get_timestamp()} [ POSSIBLE ISLAND ] Seed {seed}: {free_border_ratio:.1%} free border! -> Saved", file=sys.stdout)
                    matches.append(seed)
                    match_found = True
                    log_entry = f"{seed} - POSSIBLE ISLAND ({free_border_ratio:.1%} free border)\n"
                    
                elif free_border_ratio >= args.min_ratio:
                    match_filename = os.path.join(args.out_dir, f"PENINSULA_seed_{seed}_{int(free_border_ratio*100)}pct.png")
                    os.rename(temp_file, match_filename)
                    if save_debug_func:
                        save_debug_func(debug_file)
                    tqdm.write(f"{get_timestamp()} [ MATCH ] Seed {seed}: {free_border_ratio:.1%} free border! -> Saved", file=sys.stdout)
                    matches.append(seed)
                    match_found = True
                    log_entry = f"{seed} - PENINSULA ({free_border_ratio:.1%} free border)\n"
                    
                else:
                    if args.debug and save_debug_func:
                        save_debug_func(debug_file)
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                    
                if match_found:
                    with open(seed_log_path, "a") as f:
                        f.write(log_entry)
                    
            except KeyboardInterrupt:
                if proc and proc.poll() is None:
                    proc.terminate()
                    proc.wait()
                raise
            except Exception as e:
                tqdm.write(f"{get_timestamp()} Error analyzing seed {seed}: {e}", file=sys.stdout)
            finally:
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except OSError:
                        pass
                pbar.update(1)

    except KeyboardInterrupt:
        tqdm.write(f"\n{get_timestamp()} Scan interrupted by user. Shutting down gracefully...", file=sys.stdout)
        sys.exit(0)

    finally:
        stop_refresh.set()
        if refresher_thread.is_alive():
            refresher_thread.join()
        pbar.close()

    print(f"\n{get_timestamp()} Scan complete! Found {len(matches)} potential seeds. Logged to {seed_log_path}")

if __name__ == "__main__":
    main()
