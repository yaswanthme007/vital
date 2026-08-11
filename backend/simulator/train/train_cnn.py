"""Task 2 of S11: trains a small CNN on simulator/train/build_dataset.py's
digit-cell dataset and exports it to ONNX for app/pipeline/onnx_engine.py.

Usage:
    python -m simulator.train.train_cnn
    python -m simulator.train.train_cnn --dataset-dir simulator/train/dataset --epochs 15
"""

import argparse
import json
import os
import random
import sys
import time
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np  # noqa: E402
import torch  # noqa: E402
import torch.nn as nn  # noqa: E402
from torch.utils.data import DataLoader, TensorDataset  # noqa: E402

from simulator.train.labels import LABEL_CLASSES  # noqa: E402

DEFAULT_DATASET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset")
DEFAULT_MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "models")
CELL_SIZE = 28
SEED = 0


class DigitCNN(nn.Module):
    """A few conv layers -- deliberately small: this is 28x28 grayscale
    single glyphs, not natural images, and the S11 task caps training at
    CPU-minutes, not GPU-hours."""

    def __init__(self, num_classes: int = len(LABEL_CLASSES)):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 16, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),  # 28 -> 14
            nn.Conv2d(16, 32, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),  # 14 -> 7
            nn.Conv2d(32, 64, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),  # 7 -> 3
        )
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(64 * 3 * 3, 128),
            nn.ReLU(inplace=True),
            nn.Dropout(0.3),
            nn.Linear(128, num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.classifier(self.features(x))


def _set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def _to_tensor(X: np.ndarray) -> torch.Tensor:
    """uint8 (N, 28, 28) -> float32 (N, 1, 28, 28) scaled to [0, 1] -- the
    exact preprocessing recorded in the ONNX sidecar and replicated by
    OnnxDigitEngine at inference."""
    return torch.from_numpy(X).float().div_(255.0).unsqueeze(1)


def load_dataset(dataset_dir: str):
    npz_path = os.path.join(dataset_dir, "cells.npz")
    if not os.path.isfile(npz_path):
        raise SystemExit(f"{npz_path} not found -- run build_dataset.py first.")
    data = np.load(npz_path)
    with open(os.path.join(dataset_dir, "meta.json")) as f:
        meta = json.load(f)
    return data, meta


def _class_weights(y_train: np.ndarray, num_classes: int) -> torch.Tensor:
    """Inverse-frequency weighting -- the dataset is naturally imbalanced
    (e.g. '0' is a much rarer leading/interior digit than '1'/'9' given the
    simulator's vitals ranges), so an unweighted loss would bias the model
    toward the frequent classes."""
    counts = np.bincount(y_train, minlength=num_classes).astype(np.float64)
    counts[counts == 0] = 1.0  # avoid div-by-zero for any class absent from train
    weights = counts.sum() / (num_classes * counts)
    return torch.tensor(weights, dtype=torch.float32)


def evaluate(model: nn.Module, loader: DataLoader, device: torch.device) -> Tuple[float, np.ndarray, np.ndarray]:
    model.eval()
    correct = 0
    total = 0
    all_preds: List[int] = []
    all_true: List[int] = []
    with torch.no_grad():
        for xb, yb in loader:
            xb, yb = xb.to(device), yb.to(device)
            logits = model(xb)
            preds = logits.argmax(dim=1)
            correct += (preds == yb).sum().item()
            total += yb.size(0)
            all_preds.extend(preds.cpu().tolist())
            all_true.extend(yb.cpu().tolist())
    accuracy = correct / total if total else 0.0
    return accuracy, np.array(all_true), np.array(all_preds)


def confusion_matrix(y_true: np.ndarray, y_pred: np.ndarray, num_classes: int) -> np.ndarray:
    cm = np.zeros((num_classes, num_classes), dtype=np.int64)
    for t, p in zip(y_true, y_pred):
        cm[t, p] += 1
    return cm


def render_confusion_matrix(cm: np.ndarray, classes: List[str]) -> str:
    header = "true\\pred".rjust(9) + "".join(c.rjust(6) for c in classes)
    lines = [header]
    for i, row in enumerate(cm):
        lines.append(classes[i].rjust(9) + "".join(str(v).rjust(6) for v in row))
    return "\n".join(lines)


def subset_accuracy(y_true: np.ndarray, y_pred: np.ndarray, mask: np.ndarray) -> Tuple[int, float]:
    if mask.sum() == 0:
        return 0, float("nan")
    correct = (y_true[mask] == y_pred[mask]).sum()
    return int(mask.sum()), correct / mask.sum()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset-dir", default=DEFAULT_DATASET_DIR)
    parser.add_argument("--models-dir", default=DEFAULT_MODELS_DIR)
    parser.add_argument("--epochs", type=int, default=15)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    _set_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    data, meta = load_dataset(args.dataset_dir)
    num_classes = len(LABEL_CLASSES)

    X_train, y_train = data["X_train"], data["y_train"]
    X_val, y_val = data["X_val"], data["y_val"]
    X_test, y_test = data["X_test"], data["y_test"]
    print(f"train={len(y_train)}  val={len(y_val)}  test={len(y_test)}")

    train_loader = DataLoader(
        TensorDataset(_to_tensor(X_train), torch.from_numpy(y_train)),
        batch_size=args.batch_size,
        shuffle=True,
    )
    val_loader = DataLoader(TensorDataset(_to_tensor(X_val), torch.from_numpy(y_val)), batch_size=256)
    test_loader = DataLoader(TensorDataset(_to_tensor(X_test), torch.from_numpy(y_test)), batch_size=256)

    model = DigitCNN(num_classes=num_classes).to(device)
    weights = _class_weights(y_train, num_classes).to(device)
    criterion = nn.CrossEntropyLoss(weight=weights)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)

    best_val_acc = -1.0
    best_state = None
    started = time.time()

    for epoch in range(1, args.epochs + 1):
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
        val_acc, _, _ = evaluate(model, val_loader, device)
        print(f"epoch {epoch:2d}/{args.epochs}  train_loss={train_loss:.4f}  val_acc={val_acc:.4f}")

        if val_acc > best_val_acc:
            best_val_acc = val_acc
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

    elapsed = time.time() - started
    print(f"Training took {elapsed:.1f}s. Best val_acc={best_val_acc:.4f}")

    model.load_state_dict(best_state)

    test_acc, y_true, y_pred = evaluate(model, test_loader, device)
    test_meta = meta["test"]
    aug_levels = np.array([m["augment_level"] for m in test_meta])
    heavy_mask = np.isin(aug_levels, ["heavy", "random"])
    clean_mask = aug_levels == "none"

    heavy_n, heavy_acc = subset_accuracy(y_true, y_pred, heavy_mask)
    clean_n, clean_acc = subset_accuracy(y_true, y_pred, clean_mask)

    print()
    print(f"TEST accuracy (all {len(y_true)} cells): {test_acc:.4f}")
    print(f"TEST accuracy, heavily-augmented subset (n={heavy_n}): {heavy_acc:.4f}")
    print(f"TEST accuracy, clean subset (n={clean_n}): {clean_acc:.4f}")
    print()
    print("Confusion matrix (test set, rows=true, cols=predicted):")
    cm = confusion_matrix(y_true, y_pred, num_classes)
    print(render_confusion_matrix(cm, LABEL_CLASSES))

    os.makedirs(args.models_dir, exist_ok=True)
    onnx_path = os.path.join(args.models_dir, "digit_cnn.onnx")
    labels_path = os.path.join(args.models_dir, "digit_cnn.labels.json")
    preprocess_path = os.path.join(args.models_dir, "digit_cnn.preprocess.json")
    report_path = os.path.join(args.models_dir, "digit_cnn.train_report.json")

    model.eval().cpu()
    dummy = torch.zeros(1, 1, CELL_SIZE, CELL_SIZE)
    torch.onnx.export(
        model,
        dummy,
        onnx_path,
        input_names=["cell"],
        output_names=["logits"],
        dynamic_axes={"cell": {0: "batch"}, "logits": {0: "batch"}},
        opset_version=17,
        dynamo=False,  # the new dynamo-based exporter (torch>=2.x default) needs the
        # optional `onnxscript` package; this tiny CNN has no dynamic control flow, so
        # the legacy TorchScript-based exporter (needs only `onnx`, already a pinned
        # dependency) is simpler and sufficient.
    )

    with open(labels_path, "w") as f:
        json.dump(LABEL_CLASSES, f, indent=2)

    with open(preprocess_path, "w") as f:
        json.dump(
            {
                "cell_size": CELL_SIZE,
                "channels": 1,
                "scale": 1.0 / 255.0,
                "input_name": "cell",
                "output_name": "logits",
                "input_shape": [1, 1, CELL_SIZE, CELL_SIZE],
                "note": "input is uint8 grayscale (see app.pipeline.segment.normalize_cell), "
                "cast to float32 and divided by 255 -- no mean/std normalization.",
            },
            f,
            indent=2,
        )

    onnx_size = os.path.getsize(onnx_path)
    with open(report_path, "w") as f:
        json.dump(
            {
                "seed": args.seed,
                "epochs": args.epochs,
                "device": str(device),
                "train_seconds": round(elapsed, 1),
                "best_val_accuracy": best_val_acc,
                "test_accuracy": test_acc,
                "test_accuracy_heavily_augmented": {"n": heavy_n, "accuracy": heavy_acc},
                "test_accuracy_clean": {"n": clean_n, "accuracy": clean_acc},
                "confusion_matrix": cm.tolist(),
                "label_classes": LABEL_CLASSES,
                "onnx_size_bytes": onnx_size,
                "train_n": len(y_train),
                "val_n": len(y_val),
                "test_n": len(y_test),
            },
            f,
            indent=2,
        )

    print()
    print(f"Wrote {onnx_path}  ({onnx_size / 1024:.1f} KB)")
    print(f"Wrote {labels_path}")
    print(f"Wrote {preprocess_path}")
    print(f"Wrote {report_path}")


if __name__ == "__main__":
    main()
