import argparse
import json
import os
import random
import sys
import time

# Allow running as a plain script (`python simulator/generate.py`) as well as
# as a module (`python -m simulator.generate`).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from simulator.randomize.augment import augment_frame  # noqa: E402
from simulator.render.monitor_layout import LAYOUTS, render_monitor  # noqa: E402
from simulator.vitals_series import generate_vitals_series  # noqa: E402

DEFAULT_OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
LAYOUT_CHOICES = (*sorted(LAYOUTS), "random")
AUGMENT_CHOICES = ("none", "light", "heavy", "random")


def build_sample_reading(seed: int = None) -> dict:
    """A single realistic reading, centred on healthy intra-op values."""
    return generate_vitals_series(duration_s=1, interval_s=1.0, seed=seed)[0]


def _resolve_layout(layout: str, rng: random.Random) -> str:
    if layout == "random":
        return rng.choice(sorted(LAYOUTS))
    return layout


def _render_labeled_frame(reading: dict, png_path: str, layout: str, augment: str, seed: int) -> dict:
    """Render one frame, then (optionally) augment it in place: reload the
    saved PNG, apply randomized effects, and overwrite the PNG with the
    augmented version. Returns {"values", "rois", "augmentations"} where rois
    reflect the POST-augmentation geometry."""
    label = render_monitor(reading, png_path, layout=layout)

    if augment == "none":
        return {"values": label["values"], "rois": label["rois"], "augmentations": []}

    image = Image.open(png_path).convert("RGB")
    augmented_image, augmented_rois, applied_effects = augment_frame(image, label["rois"], level=augment, seed=seed)
    augmented_image.save(png_path)

    return {"values": label["values"], "rois": augmented_rois, "augmentations": applied_effects}


def generate_sample(
    sample_id: str, out_dir: str, layout: str = "grid", augment: str = "none", seed: int = None
) -> dict:
    """Single-frame sample: writes <out_dir>/<id>.png + <id>.json."""
    os.makedirs(out_dir, exist_ok=True)
    resolved_layout = _resolve_layout(layout, random.Random(seed))

    reading = build_sample_reading(seed=seed)
    png_path = os.path.join(out_dir, f"{sample_id}.png")
    json_path = os.path.join(out_dir, f"{sample_id}.json")

    frame = _render_labeled_frame(reading, png_path, resolved_layout, augment, seed)

    record = {
        "values": frame["values"],
        "rois": frame["rois"],
        "layout": resolved_layout,
        "augmentLevel": augment,
        "augmentations": frame["augmentations"],
        "timestamp": reading["timestamp"],
    }

    with open(json_path, "w") as f:
        json.dump(record, f, indent=2)

    return {"png_path": png_path, "json_path": json_path, "record": record}


def generate_case(
    sample_id: str, out_dir: str, frames: int, layout: str = "grid", augment: str = "none", seed: int = None
) -> dict:
    """Multi-frame case: one monitor (one layout), a drifting vitals series,
    one labeled PNG+JSON per frame under <out_dir>/<id>/."""
    case_dir = os.path.join(out_dir, sample_id)
    os.makedirs(case_dir, exist_ok=True)

    base_rng = random.Random(seed)
    resolved_layout = _resolve_layout(layout, base_rng)
    readings = generate_vitals_series(duration_s=frames, interval_s=1.0, seed=seed)

    frame_records = []
    for i, reading in enumerate(readings):
        frame_name = f"frame_{i:04d}"
        png_path = os.path.join(case_dir, f"{frame_name}.png")
        json_path = os.path.join(case_dir, f"{frame_name}.json")

        frame_seed = None if seed is None else seed + i
        frame = _render_labeled_frame(reading, png_path, resolved_layout, augment, frame_seed)
        record = {
            "values": frame["values"],
            "rois": frame["rois"],
            "layout": resolved_layout,
            "augmentLevel": augment,
            "augmentations": frame["augmentations"],
            "timestamp": reading["timestamp"],
        }
        with open(json_path, "w") as f:
            json.dump(record, f, indent=2)

        frame_records.append({"frame": frame_name, "png": png_path, "json": json_path})

    case_record = {
        "id": sample_id,
        "layout": resolved_layout,
        "frame_count": len(readings),
        "frames": frame_records,
    }
    with open(os.path.join(case_dir, "case.json"), "w") as f:
        json.dump(case_record, f, indent=2)

    return case_record


def generate_dataset(
    dataset_id: str, out_dir: str, count: int, layout: str = "grid", augment: str = "none", seed: int = None
) -> dict:
    """Batch dataset: `count` labeled samples under <out_dir>/<id>/, each with
    an independently resolved layout (per `layout`), a reading sampled from a
    short drifting series, and randomized augmentation. Writes manifest.json
    indexing every sample plus the layout/augmentation distribution."""
    dataset_dir = os.path.join(out_dir, dataset_id)
    os.makedirs(dataset_dir, exist_ok=True)

    base_rng = random.Random(seed)

    samples = []
    layout_distribution: dict = {}
    augmentation_distribution: dict = {}

    for i in range(count):
        sample_seed = base_rng.randint(0, 2**32 - 1)
        sample_rng = random.Random(sample_seed)

        resolved_layout = _resolve_layout(layout, sample_rng)
        layout_distribution[resolved_layout] = layout_distribution.get(resolved_layout, 0) + 1

        # Source the frame from a short drifting series (not just the first,
        # static reading) so the dataset covers physiologically drifted states.
        duration_s = sample_rng.randint(1, 180)
        reading = generate_vitals_series(duration_s=duration_s, interval_s=1.0, seed=sample_seed)[-1]

        sample_name = f"sample_{i:04d}"
        png_path = os.path.join(dataset_dir, f"{sample_name}.png")
        json_path = os.path.join(dataset_dir, f"{sample_name}.json")

        frame = _render_labeled_frame(reading, png_path, resolved_layout, augment, sample_seed)

        effect_types = [effect["type"] for effect in frame["augmentations"]]
        if effect_types:
            for effect_type in effect_types:
                augmentation_distribution[effect_type] = augmentation_distribution.get(effect_type, 0) + 1
        else:
            augmentation_distribution["none"] = augmentation_distribution.get("none", 0) + 1

        record = {
            "id": sample_name,
            "values": frame["values"],
            "rois": frame["rois"],
            "layout": resolved_layout,
            "augmentLevel": augment,
            "augmentations": frame["augmentations"],
            "timestamp": reading["timestamp"],
        }
        with open(json_path, "w") as f:
            json.dump(record, f, indent=2)

        samples.append(
            {
                "id": sample_name,
                "png": png_path,
                "json": json_path,
                "layout": resolved_layout,
                "augmentations": effect_types,
            }
        )

    manifest = {
        "dataset_id": dataset_id,
        "count": count,
        "seed": seed,
        "layout": layout,
        "augment": augment,
        "layout_distribution": layout_distribution,
        "augmentation_distribution": augmentation_distribution,
        "samples": samples,
    }
    manifest_path = os.path.join(dataset_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    manifest["manifest_path"] = manifest_path
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic anaesthesia-monitor frame(s) plus ground-truth label(s)."
    )
    parser.add_argument("--id", default=None, help="Sample/case/dataset id (defaults to a timestamp-based id)")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducible values")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="Output directory for generated files")
    parser.add_argument("--layout", choices=LAYOUT_CHOICES, default="grid", help="Monitor layout to render")
    parser.add_argument(
        "--augment", choices=AUGMENT_CHOICES, default="none",
        help="Camera/lighting augmentation severity (perspective/glare/dim/blur/noise/occlusion)",
    )
    parser.add_argument(
        "--frames", type=int, default=1,
        help="Number of frames (seconds) to render as a drifting case sequence; 1 renders a single sample",
    )
    parser.add_argument(
        "--count", type=int, default=None,
        help="Generate a labeled dataset of N samples (random per-sample layout/drift) instead of a single sample/case",
    )
    args = parser.parse_args()

    sample_id = args.id or f"sample-{int(time.time() * 1000)}"

    if args.count:
        result = generate_dataset(
            sample_id, args.out_dir, args.count, layout=args.layout, augment=args.augment, seed=args.seed
        )
        print(
            f"Wrote dataset '{sample_id}' ({result['count']} samples) to "
            f"{os.path.join(args.out_dir, sample_id)}"
        )
        print(json.dumps({k: v for k, v in result.items() if k != "samples"}, indent=2))
    elif args.frames > 1:
        result = generate_case(sample_id, args.out_dir, args.frames, layout=args.layout, augment=args.augment, seed=args.seed)
        case_dir = os.path.join(args.out_dir, sample_id)
        print(f"Wrote case '{sample_id}' ({result['frame_count']} frames, layout={result['layout']}) to {case_dir}")
        print(json.dumps(result, indent=2))
    else:
        result = generate_sample(sample_id, args.out_dir, layout=args.layout, augment=args.augment, seed=args.seed)
        print(f"Wrote {result['png_path']}")
        print(f"Wrote {result['json_path']}")
        print(json.dumps(result["record"], indent=2))


if __name__ == "__main__":
    main()
