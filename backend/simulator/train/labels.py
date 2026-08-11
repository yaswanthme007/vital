"""Single source of truth for the CNN's class list, used by build_dataset.py
(labeling) and train_cnn.py (training + ONNX export). Deliberately NOT
imported by app/pipeline/onnx_engine.py at runtime -- the exported ONNX
model carries this same list out as a JSON sidecar (models/digit_cnn.labels.json)
so the deployed app never needs a dependency on simulator/ (dev/training-only
tooling) to do inference.
"""

# '/' separates NIBP's systolic/diastolic; '.' is temp's decimal point;
# 'blank' is a synthetic negative class for segmentation artifacts (an
# over/under-segmented sliver that isn't really a glyph) -- see
# build_dataset.py's _synthesize_blank_cells.
LABEL_CLASSES = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "/", ".", "blank"]

BLANK_LABEL = "blank"
