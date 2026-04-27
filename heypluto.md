# Train `hey_pluto.onnx`

Use this runbook when you need to recreate the custom `hey pluto` openWakeWord model from a fresh Google Colab runtime.

## 1. Start Colab

1. Open a new Google Colab notebook.
2. Go to `Runtime` -> `Change runtime type`.
3. Select `T4 GPU`.
4. Run the cells below in order.

## 2. Clone Repos

```python
!git clone https://github.com/dscripka/openWakeWord openwakeword
!git clone https://github.com/dscripka/piper-sample-generator piper-sample-generator
```

## 3. Install Dependencies

```python
!pip install -e ./openwakeword
!pip install -r piper-sample-generator/requirements.txt
!pip install torchinfo torchmetrics pronouncing speechbrain acoustics mutagen torch-audiomentations audiomentations datasets scipy scikit-learn librosa soundfile onnxscript
!apt-get update
!apt-get install -y espeak-ng libespeak-ng1
```

## 4. Download Piper Voice Model

```python
!mkdir -p piper-sample-generator/models
!wget -O piper-sample-generator/models/en-us-libritts-high.pt 'https://github.com/rhasspy/piper-sample-generator/releases/download/v1.0.0/en-us-libritts-high.pt'
!wget -O piper-sample-generator/models/en-us-libritts-high.pt.json 'https://raw.githubusercontent.com/rhasspy/piper-sample-generator/master/models/en-us-libritts-high.pt.json'
```

## 5. Apply Colab Compatibility Patches

```python
from pathlib import Path
import site

# PyTorch 2.6+ changed torch.load defaults.
p = Path("piper-sample-generator/generate_samples.py")
text = p.read_text()
text = text.replace(
    "model = torch.load(model_path)",
    "model = torch.load(model_path, weights_only=False)"
)
p.write_text(text)

# Newer torchaudio removed a couple APIs used by torch_audiomentations.
for package_dir in site.getsitepackages():
    path = Path(package_dir) / "torch_audiomentations" / "utils" / "io.py"
    if path.exists():
        text = path.read_text()
        text = text.replace(
            'torchaudio.set_audio_backend("soundfile")',
            'if hasattr(torchaudio, "set_audio_backend"):\n    torchaudio.set_audio_backend("soundfile")'
        )
        text = text.replace(
            "        info = torchaudio.info(file_path)",
            '''        if hasattr(torchaudio, "info"):
            info = torchaudio.info(file_path)
        else:
            import soundfile as sf
            from types import SimpleNamespace
            sf_info = sf.info(file_path)
            info = SimpleNamespace(num_frames=sf_info.frames, sample_rate=sf_info.samplerate)'''
        )
        path.write_text(text)
        print("patched", path)
```

## 6. Download openWakeWord Feature Models

```python
from openwakeword.utils import download_models

download_models()
!ls -lh openwakeword/openwakeword/resources/models
```

Expected files include:

```text
melspectrogram.onnx
embedding_model.onnx
```

## 7. Create Training Config

```python
import yaml

config = {
    "target_phrase": ["hey pluto"],
    "model_name": "hey_pluto",
    "output_dir": "/content/my_custom_model",

    "piper_sample_generator_path": "./piper-sample-generator",
    "piper_model": "en-us-libritts-high",
    "tts_batch_size": 50,

    "n_samples": 1000,
    "n_samples_val": 1000,

    "steps": 10000,
    "target_accuracy": 0.6,
    "target_recall": 0.25,
    "target_false_positives_per_hour": 0.5,
    "max_negative_weight": 1500,
    "model_type": "dnn",
    "layer_size": 32,

    "augmentation_batch_size": 16,
    "augmentation_rounds": 1,
    "batch_n_per_class": {
        "positive": 50,
        "adversarial_negative": 50,
    },

    "custom_negative_phrases": [],
    "feature_data_files": {},
    "false_positive_validation_data_path": "",

    "background_paths": [],
    "background_paths_duplication_rate": [],
    "rir_paths": [],
}

with open("my_model.yaml", "w") as file:
    yaml.dump(config, file)

print(open("my_model.yaml").read())
```

## 8. Generate Clips

```python
import sys
!{sys.executable} openwakeword/openwakeword/train.py --training_config my_model.yaml --generate_clips
```

Expected end:

```text
generate_samples:Done
```

## 9. Augment Clips

```python
!find /content/my_custom_model -type f -name '*.npy' -delete

import sys
!{sys.executable} openwakeword/openwakeword/train.py --training_config my_model.yaml --augment_clips
```

## 10. Train And Export

First create a continuous false-positive validation feature file.

Important: do not point `false_positive_validation_data_path` directly at `negative_features_test.npy`. That file is already windowed as `(N, 16, 96)`, and the training script will window it again. Convert it to `(N, 96)` first:

```python
import glob
import yaml
import numpy as np

with open("my_model.yaml", "r") as file:
    config = yaml.safe_load(file)

negative_candidates = sorted(glob.glob("/content/my_custom_model/**/negative_features_test.npy", recursive=True))
if not negative_candidates:
    raise RuntimeError("No negative_features_test.npy found. Rerun the augment step first.")

negative_path = negative_candidates[0]
negative = np.load(negative_path)
print("Loaded", negative_path, negative.shape)

if negative.ndim != 3 or negative.shape[-1] != 96:
    raise RuntimeError(f"Unexpected negative feature shape: {negative.shape}")

continuous = negative.reshape(-1, 96)
fp_path = "/content/my_custom_model/hey_pluto/false_positive_validation_features.npy"
np.save(fp_path, continuous)

config["false_positive_validation_data_path"] = fp_path

with open("my_model.yaml", "w") as file:
    yaml.dump(config, file)

print("Saved", fp_path, continuous.shape)
print("Using false_positive_validation_data_path =", fp_path)
```

Then train:

```python
import sys
!{sys.executable} openwakeword/openwakeword/train.py --training_config my_model.yaml --train_model
```

If the final output complains about `.tflite` or `onnx_tf`, that is okay. Pluto only needs the ONNX model.

## 11. Download Model Files

Run this cell:

```python
from google.colab import files
import os

onnx_path = "/content/my_custom_model/hey_pluto.onnx"
data_path = "/content/my_custom_model/hey_pluto.onnx.data"

print("ONNX exists:", os.path.exists(onnx_path), os.path.getsize(onnx_path) if os.path.exists(onnx_path) else None)
print("DATA exists:", os.path.exists(data_path), os.path.getsize(data_path) if os.path.exists(data_path) else None)

files.download(onnx_path)

if os.path.exists(data_path):
    files.download(data_path)
```

Important: if Colab downloads both files, keep both:

```text
hey_pluto.onnx
hey_pluto.onnx.data
```

The `.onnx` file may reference `.onnx.data`. If that happens, Pluto will fail unless both files are in the same folder.

## 12. Install Model In Pluto

On your Mac:

```bash
mkdir -p /Users/pardhuvarma/voice-pluto/app/audio/models
mv ~/Downloads/hey_pluto.onnx /Users/pardhuvarma/voice-pluto/app/audio/models/hey_pluto.onnx
```

If you downloaded the sidecar data file:

```bash
mv ~/Downloads/hey_pluto.onnx.data /Users/pardhuvarma/voice-pluto/app/audio/models/hey_pluto.onnx.data
```

Verify:

```bash
ls -lh /Users/pardhuvarma/voice-pluto/app/audio/models
```

## 13. Configure `.env`

```env
PLUTO_WAKEWORD_MODELS=hey_pluto
PLUTO_WAKEWORD_MODEL_DIR=./app/audio/models
PLUTO_WAKEWORD_THRESHOLD=0.6
```

If it misses too often, try:

```env
PLUTO_WAKEWORD_THRESHOLD=0.5
```

If it triggers accidentally, try:

```env
PLUTO_WAKEWORD_THRESHOLD=0.7
```

## 14. Test Pluto

```bash
cd /Users/pardhuvarma/voice-pluto
source .venv/bin/activate
python -m app.main --mode listen --debug
```

Say:

```text
hey pluto
open browser
```

Expected:

- Wakeword is detected.
- Transcript appears in debug logs.
- Chrome opens.

## Common Failure

If you see:

```text
No such file or directory ["app/audio/models/hey_pluto.onnx.data"]
```

Fix:

```bash
mv ~/Downloads/hey_pluto.onnx.data /Users/pardhuvarma/voice-pluto/app/audio/models/hey_pluto.onnx.data
```

Then rerun Pluto.
