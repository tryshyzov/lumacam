# Models

Two small models from Google's MediaPipe, used by `camera_studio/effects.py`
for background effects and face framing. Both are under the Apache License
2.0 (https://www.apache.org/licenses/LICENSE-2.0), like MediaPipe itself.

| File | From | Changes |
| --- | --- | --- |
| `selfie_segmenter.onnx` | `selfie_segmenter.tflite` (image_segmenter, float16, from storage.googleapis.com/mediapipe-models) | Converted to ONNX with tf2onnx (opset 17). Its last layer, MediaPipe's custom `Convolution2DTransposeBias`, is replaced with the standard `ConvTranspose` (2×2, stride 2) plus the same bias; checked equal to the original layer's formula. |
| `face_detector.onnx` | `blaze_face_short_range.tflite` (face_detector, float16, same place) | Converted to ONNX with tf2onnx (opset 17). |

The segmenter takes a 256×256 RGB picture in 0…1 and gives the chance of a
person per pixel. The face detector takes a 128×128 RGB picture in −1…1 and
gives 896 boxes against its anchors (see `effects.face_anchors`).
