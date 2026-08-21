"""M2 Phase 2/3/6: FieldCNN -- the Tier-2 field classifier.

candidate crop (64x64 grayscale, letterboxed -- see tier2_field_dataset.py)
    -> {hr, spo2, nibp, etco2, temp, rr, not_a_vital}

Architecture note (per the M2 spec's explicit instruction not to blindly
reuse the digit CNN): simulator/train/train_cnn.py's DigitCNN is a 28x28
single-GLYPH classifier over 13 classes (10 digits + '/' + '.' + 'blank') --
its whole job is "which character is this", on a tightly cropped, already-
segmented, single-glyph binary-ish cell. This is a different problem: a
candidate crop is a whole multi-glyph FIELD (a 3-digit HR reading, an NIBP
two-line block, or a false-positive UI panel), at a bigger, more variable
size, where the answer depends on layout/context (how many digits, is there
a '/' pattern, is it text-dense vs a waveform's sparse trace) not just glyph
shape. FieldCNN below reuses DigitCNN's *style* (small conv stack, ONNX
export via torch.onnx.export, the same class-weighted-CrossEntropy +
best-checkpoint-by-validation-metric training idiom) but is a distinct,
bigger-input (64x64), deeper (4 conv blocks + BatchNorm), 7-class network --
not a relabeled DigitCNN.

Usage:
    python -m app.eval.tier2_field_classifier
    python -m app.eval.tier2_field_classifier --epochs 40
"""

import argparse
import json
import os
import random
import time
from typing import Dict, List, Tuple

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from app.eval.tier2_field_dataset import CLASSES, CROP_SIZE

DEFAULT_DATASET_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "tier2_data", "external_monitor_video", "tier2_field_dataset"
)
DEFAULT_MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "models")
SEED = 0


class FieldCNN(nn.Module):
    """4 conv blocks (with BatchNorm -- real photographic/JPEG noise makes
    training less stable than the synthetic digit dataset, BN helps), input
    64x64x1, output num_classes logits. Deliberately still small (~280K
    params) relative to natural-image CNNs: the training set here is real,
    hand-annotated, and small (a few hundred crops even after bounded
    augmentation) -- a bigger network would just memorize it."""

    def __init__(self, num_classes: int = len(CLASSES)):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 16, 3, padding=1),
            nn.BatchNorm2d(16),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),  # 64 -> 32
            nn.Conv2d(16, 32, 3, padding=1),
            nn.BatchNorm2d(32),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),  # 32 -> 16
            nn.Conv2d(32, 64, 3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),  # 16 -> 8
            nn.Conv2d(64, 96, 3, padding=1),
            nn.BatchNorm2d(96),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),  # 8 -> 4
        )
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(96 * 4 * 4, 128),
            nn.ReLU(inplace=True),
            nn.Dropout(0.45),
            nn.Linear(128, num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.classifier(self.features(x))


def _set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def _to_tensor(X: np.ndarray) -> torch.Tensor:
    return torch.from_numpy(X).float().div_(255.0).unsqueeze(1)


def load_dataset(dataset_dir: str):
    npz_path = os.path.join(dataset_dir, "field_crops.npz")
    if not os.path.isfile(npz_path):
        raise SystemExit(f"{npz_path} not found -- run tier2_field_dataset.py first.")
    return np.load(npz_path)


def _class_weights(y_train: np.ndarray, num_classes: int) -> torch.Tensor:
    """Inverse-frequency weighting, same idiom as train_cnn.py's
    _class_weights -- applied on top of tier2_field_dataset.py's own
    bounded-subsample/oversample balancing to absorb whatever imbalance
    remains after that (e.g. 'temp' still has more real examples than the
    floor for other vitals)."""
    counts = np.bincount(y_train, minlength=num_classes).astype(np.float64)
    counts[counts == 0] = 1.0
    weights = counts.sum() / (num_classes * counts)
    return torch.tensor(weights, dtype=torch.float32)


@torch.no_grad()
def predict_all(model: nn.Module, loader: DataLoader, device: torch.device) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    model.eval()
    all_true, all_pred, all_conf = [], [], []
    for xb, yb in loader:
        xb = xb.to(device)
        logits = model(xb)
        probs = torch.softmax(logits, dim=1)
        conf, pred = probs.max(dim=1)
        all_true.extend(yb.tolist())
        all_pred.extend(pred.cpu().tolist())
        all_conf.extend(conf.cpu().tolist())
    return np.array(all_true), np.array(all_pred), np.array(all_conf)


def confusion_matrix(y_true: np.ndarray, y_pred: np.ndarray, num_classes: int) -> np.ndarray:
    cm = np.zeros((num_classes, num_classes), dtype=np.int64)
    for t, p in zip(y_true, y_pred):
        cm[t, p] += 1
    return cm


def per_class_prf(cm: np.ndarray, classes: List[str]) -> Dict[str, dict]:
    """Precision/recall/F1 per class from the confusion matrix (rows=true,
    cols=pred). A class absent from the evaluated split (zero support) gets
    recall/F1 reported as null rather than a misleading 0.0 or 1.0 -- there
    is nothing to recall."""
    out = {}
    n = cm.shape[0]
    for i, c in enumerate(classes):
        tp = int(cm[i, i])
        fp = int(cm[:, i].sum() - tp)
        fn = int(cm[i, :].sum() - tp)
        support = int(cm[i, :].sum())
        precision = tp / (tp + fp) if (tp + fp) > 0 else (None if fp == 0 and tp == 0 else 0.0)
        recall = tp / (tp + fn) if support > 0 else None
        if precision is not None and recall is not None and (precision + recall) > 0:
            f1 = 2 * precision * recall / (precision + recall)
        elif support == 0 and (tp + fp) == 0:
            f1 = None
        else:
            f1 = 0.0
        out[c] = {"support": support, "precision": precision, "recall": recall, "f1": f1}
    return out


def macro_weighted_f1(prf: Dict[str, dict]) -> Tuple[float, float]:
    scored = [(v["f1"], v["support"]) for v in prf.values() if v["f1"] is not None]
    if not scored:
        return 0.0, 0.0
    macro = sum(f1 for f1, _ in scored) / len(scored)
    total_support = sum(s for _, s in scored)
    weighted = (sum(f1 * s for f1, s in scored) / total_support) if total_support else macro
    return macro, weighted


def render_confusion_matrix(cm: np.ndarray, classes: List[str]) -> str:
    header = "true\\pred".rjust(12) + "".join(c[:8].rjust(10) for c in classes)
    lines = [header]
    for i, row in enumerate(cm):
        lines.append(classes[i][:11].rjust(12) + "".join(str(v).rjust(10) for v in row))
    return "\n".join(lines)


def render_prf_table(prf: Dict[str, dict]) -> str:
    lines = [f"{'class':12s}{'support':>8s}{'precision':>11s}{'recall':>9s}{'f1':>9s}"]
    for c, v in prf.items():
        p = f"{v['precision']:.3f}" if v["precision"] is not None else "n/a"
        r = f"{v['recall']:.3f}" if v["recall"] is not None else "n/a"
        f1 = f"{v['f1']:.3f}" if v["f1"] is not None else "n/a"
        lines.append(f"{c:12s}{v['support']:>8d}{p:>11s}{r:>9s}{f1:>9s}")
    return "\n".join(lines)


def train(
    dataset_dir: str,
    models_dir: str,
    epochs: int = 40,
    batch_size: int = 32,
    lr: float = 1e-3,
    weight_decay: float = 1e-4,
    seed: int = SEED,
) -> dict:
    _set_seed(seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    data = load_dataset(dataset_dir)
    num_classes = len(CLASSES)
    X_train, y_train = data["X_train"], data["y_train"]
    X_val, y_val = data["X_val"], data["y_val"]
    X_test, y_test = data["X_test"], data["y_test"]
    print(f"train={len(y_train)}  val={len(y_val)}  test={len(y_test)}")

    train_loader = DataLoader(TensorDataset(_to_tensor(X_train), torch.from_numpy(y_train)), batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(TensorDataset(_to_tensor(X_val), torch.from_numpy(y_val)), batch_size=128)
    test_loader = DataLoader(TensorDataset(_to_tensor(X_test), torch.from_numpy(y_test)), batch_size=128)

    model = FieldCNN(num_classes=num_classes).to(device)
    weights = _class_weights(y_train, num_classes).to(device)
    criterion = nn.CrossEntropyLoss(weight=weights)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)

    best_val_macro_f1 = -1.0
    best_state = None
    best_epoch = -1
    curves = []
    started = time.time()

    for epoch in range(1, epochs + 1):
        model.train()
        running_loss = 0.0
        for xb, yb in train_loader:
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad()
            loss = criterion(model(xb), yb)
            loss.backward()
            optimizer.step()
            running_loss += loss.item() * xb.size(0)
        train_loss = running_loss / len(train_loader.dataset)

        model.eval()
        val_loss_sum = 0.0
        with torch.no_grad():
            for xb, yb in val_loader:
                xb, yb = xb.to(device), yb.to(device)
                val_loss_sum += criterion(model(xb), yb).item() * xb.size(0)
        val_loss = val_loss_sum / len(val_loader.dataset) if len(val_loader.dataset) else float("nan")

        yt, yp, _ = predict_all(model, val_loader, device)
        val_cm = confusion_matrix(yt, yp, num_classes)
        val_prf = per_class_prf(val_cm, CLASSES)
        val_macro_f1, val_weighted_f1 = macro_weighted_f1(val_prf)

        curves.append(
            {"epoch": epoch, "train_loss": train_loss, "val_loss": val_loss, "val_macro_f1": val_macro_f1, "val_weighted_f1": val_weighted_f1}
        )
        print(f"epoch {epoch:2d}/{epochs}  train_loss={train_loss:.4f}  val_loss={val_loss:.4f}  val_macro_f1={val_macro_f1:.4f}")

        if val_macro_f1 > best_val_macro_f1:
            best_val_macro_f1 = val_macro_f1
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            best_epoch = epoch

    elapsed = time.time() - started
    print(f"Training took {elapsed:.1f}s. Best val_macro_f1={best_val_macro_f1:.4f} at epoch {best_epoch}")
    model.load_state_dict(best_state)

    # ── Held-out TEST -- evaluated exactly once, here ──
    yt_test, yp_test, yc_test = predict_all(model, test_loader, device)
    test_cm = confusion_matrix(yt_test, yp_test, num_classes)
    test_prf = per_class_prf(test_cm, CLASSES)
    test_macro_f1, test_weighted_f1 = macro_weighted_f1(test_prf)
    test_overall_acc = float((yt_test == yp_test).mean()) if len(yt_test) else 0.0

    print()
    print(f"TEST overall accuracy: {test_overall_acc:.4f}   macro F1: {test_macro_f1:.4f}   weighted F1: {test_weighted_f1:.4f}")
    print("TEST confusion matrix (rows=true, cols=predicted):")
    print(render_confusion_matrix(test_cm, CLASSES))
    print()
    print("TEST per-class precision/recall/F1:")
    print(render_prf_table(test_prf))

    os.makedirs(models_dir, exist_ok=True)
    onnx_path = os.path.join(models_dir, "field_classifier.onnx")
    labels_path = os.path.join(models_dir, "field_classifier.labels.json")
    preprocess_path = os.path.join(models_dir, "field_classifier.preprocess.json")
    report_path = os.path.join(models_dir, "field_classifier.train_report.json")

    model.eval().cpu()
    dummy = torch.zeros(1, 1, CROP_SIZE, CROP_SIZE)
    torch.onnx.export(
        model,
        dummy,
        onnx_path,
        input_names=["crop"],
        output_names=["logits"],
        dynamic_axes={"crop": {0: "batch"}, "logits": {0: "batch"}},
        opset_version=17,
        dynamo=False,
    )

    with open(labels_path, "w") as f:
        json.dump(CLASSES, f, indent=2)
    with open(preprocess_path, "w") as f:
        json.dump(
            {
                "crop_size": CROP_SIZE,
                "channels": 1,
                "scale": 1.0 / 255.0,
                "input_name": "crop",
                "output_name": "logits",
                "input_shape": [1, 1, CROP_SIZE, CROP_SIZE],
                "note": "input is uint8 grayscale, letterbox-normalized by "
                "app.eval.tier2_field_dataset._letterbox_gray (aspect-preserving "
                "resize onto a black CROP_SIZExCROP_SIZE canvas, centered), then "
                "cast to float32 and divided by 255 -- no mean/std normalization, "
                "same convention as models/digit_cnn.preprocess.json.",
            },
            f,
            indent=2,
        )

    onnx_agreement = verify_onnx_agreement(model, onnx_path, X_test, y_test)
    print()
    print(f"ONNX vs native agreement (test set, n={onnx_agreement['n']}):")
    print(f"  prediction agreement rate: {onnx_agreement['prediction_agreement_rate']:.4f}")
    print(f"  max |prob diff|: {onnx_agreement['max_abs_probability_diff']:.2e}   mean |prob diff|: {onnx_agreement['mean_abs_probability_diff']:.2e}")
    print(f"  within tolerance ({onnx_agreement['tolerance']:.0e}): {onnx_agreement['within_tolerance']}")

    report = {
        "seed": seed,
        "epochs": epochs,
        "best_epoch": best_epoch,
        "device": str(device),
        "train_seconds": round(elapsed, 1),
        "best_val_macro_f1": best_val_macro_f1,
        "train_n": int(len(y_train)),
        "val_n": int(len(y_val)),
        "test_n": int(len(y_test)),
        "classes": CLASSES,
        "training_curves": curves,
        "test": {
            "overall_accuracy": test_overall_acc,
            "macro_f1": test_macro_f1,
            "weighted_f1": test_weighted_f1,
            "confusion_matrix": test_cm.tolist(),
            "per_class": test_prf,
        },
        "onnx_agreement": onnx_agreement,
        "onnx_size_bytes": os.path.getsize(onnx_path),
    }
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    print()
    print(f"Wrote {onnx_path}  ({report['onnx_size_bytes'] / 1024:.1f} KB)")
    print(f"Wrote {labels_path}")
    print(f"Wrote {preprocess_path}")
    print(f"Wrote {report_path}")

    return {
        "model": model,
        "report": report,
        "test_predictions": {"y_true": yt_test, "y_pred": yp_test, "confidence": yc_test},
        "onnx_path": onnx_path,
    }


def verify_onnx_agreement(model: nn.Module, onnx_path: str, X_test: np.ndarray, y_test: np.ndarray, prob_tol: float = 1e-4) -> dict:
    """Phase 6: PyTorch (native, CPU, eval mode) vs ONNX Runtime inference on
    the SAME held-out test crops. Reported, not assumed -- opset 17 CPU
    execution of a plain conv/BN/linear stack should agree near-exactly, but
    this measures it rather than taking that on faith."""
    import onnxruntime as ort

    model.eval()
    with torch.no_grad():
        logits_native = model(_to_tensor(X_test)).numpy()
    probs_native = _softmax(logits_native)
    pred_native = probs_native.argmax(axis=1)

    session = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    batch = (X_test.astype(np.float32) / 255.0)[:, None, :, :]
    (logits_onnx,) = session.run(None, {"crop": batch})
    probs_onnx = _softmax(logits_onnx)
    pred_onnx = probs_onnx.argmax(axis=1)

    pred_agreement = float((pred_native == pred_onnx).mean()) if len(pred_native) else 1.0
    max_abs_prob_diff = float(np.max(np.abs(probs_native - probs_onnx))) if len(pred_native) else 0.0
    mean_abs_prob_diff = float(np.mean(np.abs(probs_native - probs_onnx))) if len(pred_native) else 0.0
    within_tol = max_abs_prob_diff <= prob_tol

    return {
        "n": int(len(y_test)),
        "prediction_agreement_rate": pred_agreement,
        "max_abs_probability_diff": max_abs_prob_diff,
        "mean_abs_probability_diff": mean_abs_prob_diff,
        "tolerance": prob_tol,
        "within_tolerance": within_tol,
    }


def _softmax(logits: np.ndarray) -> np.ndarray:
    e = np.exp(logits - logits.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset-dir", default=DEFAULT_DATASET_DIR)
    parser.add_argument("--models-dir", default=DEFAULT_MODELS_DIR)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    train(args.dataset_dir, args.models_dir, args.epochs, args.batch_size, args.lr, args.weight_decay, args.seed)


if __name__ == "__main__":
    main()
