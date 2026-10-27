import tempfile
import time
import unittest
from pathlib import Path

import av
import numpy as np
from PIL import Image

from camera_studio import effects
from camera_studio.effects import EffectSettings, Effects, decode_faces, face_anchors, framing_box


def stream_frame(picture: Image.Image) -> av.VideoFrame:
    """As from the phone: upside down, in YUV."""
    return av.VideoFrame.from_image(picture.transpose(Image.Transpose.ROTATE_180)).reformat(format="yuv420p")


class FaceMathTests(unittest.TestCase):
    def test_has_the_models_anchors(self) -> None:
        anchors = face_anchors()
        self.assertEqual(anchors.shape, (896, 2))
        np.testing.assert_allclose(anchors[0], [1 / 32, 1 / 32])
        np.testing.assert_allclose(anchors[512], [1 / 16, 1 / 16])

    def test_decodes_the_best_face(self) -> None:
        anchors = face_anchors()
        regressors = np.zeros((1, 896, 16), np.float32)
        scores = np.full((1, 896, 1), -10, np.float32)
        regressors[0, 600, :4] = [12.8, -6.4, 32, 40]  # 0.1 right, 0.05 up; 0.25 × 0.3125
        scores[0, 600, 0] = 4
        scores[0, 10, 0] = 1
        faces = decode_faces(regressors, scores, anchors)
        self.assertEqual(len(faces), 2)
        score, cx, cy, width, height = faces[0]
        self.assertGreater(score, 0.95)
        self.assertAlmostEqual(cx, anchors[600, 0] + 0.1, places=5)
        self.assertAlmostEqual(cy, anchors[600, 1] - 0.05, places=5)
        self.assertAlmostEqual(width, 0.25)
        self.assertAlmostEqual(height, 0.3125)

    def test_frames_a_face_in_the_upper_part_and_stays_inside(self) -> None:
        left, top, width, height = framing_box((640, 300, 120, 150), 1280, 720)
        self.assertAlmostEqual(width / height, 1280 / 720)
        self.assertGreaterEqual(height, 720 / effects.MAX_ZOOM - 1e-6)
        self.assertLess(top, 300 - 150 / 2)
        # A face at the corner: the box stays inside the picture.
        left, top, width, height = framing_box((10, 10, 60, 60), 1280, 720)
        self.assertEqual((left, top), (0.0, 0.0))
        self.assertEqual(framing_box(None, 1280, 720), (0.0, 0.0, 1280.0, 720.0))

    def test_settings_say_when_there_is_work(self) -> None:
        self.assertFalse(EffectSettings().active)
        self.assertTrue(EffectSettings(background="blur").active)
        self.assertFalse(EffectSettings(background="image").active)
        self.assertTrue(EffectSettings(framing=True).active)


class EffectsTests(unittest.TestCase):
    def picture(self) -> Image.Image:
        picture = Image.new("RGB", (1920, 1080), (40, 120, 200))
        picture.paste((230, 30, 30), (0, 0, 200, 100))  # a red corner, top left once upright
        return picture

    def test_without_effects_frames_pass_through(self) -> None:
        frame = stream_frame(self.picture())
        self.assertIs(Effects().apply(frame), frame)

    def test_blur_keeps_size_and_orientation_and_is_quick(self) -> None:
        engine = Effects()
        engine.configure(EffectSettings(background="blur", blur=0.8))
        frame = stream_frame(self.picture())
        out = engine.apply(frame)
        self.assertEqual((out.width, out.height), (1280, 720))
        upright = out.to_image().transpose(Image.Transpose.ROTATE_180)
        r, g, b = upright.getpixel((20, 20))
        self.assertGreater(r, g)  # the red corner is still top left
        started = time.perf_counter()
        for _ in range(10):
            engine.apply(frame)
        # Only catches a slow path; CI machines vary (about 10 ms on a Mac, 50 on CI's Intel).
        self.assertLess((time.perf_counter() - started) / 10, 0.25)

    def test_a_picture_goes_behind_the_person(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            behind = Path(folder) / "behind.png"
            Image.new("RGB", (400, 300), (0, 255, 0)).save(behind)
            engine = Effects()
            engine.configure(EffectSettings(background="image", image=str(behind)))
            out = engine.apply(stream_frame(self.picture()))
        # Nobody in this picture: it is all background, now green.
        r, g, b = out.to_image().getpixel((640, 360))
        self.assertGreater(g, 200)
        self.assertLess(r, 60)

    def test_framing_without_a_face_shows_everything(self) -> None:
        engine = Effects()
        engine.configure(EffectSettings(framing=True))
        frame = stream_frame(self.picture())
        for _ in range(5):
            out = engine.apply(frame)
        upright = out.to_image().transpose(Image.Transpose.ROTATE_180)
        self.assertGreater(upright.getpixel((20, 20))[0], 200)


if __name__ == "__main__":
    unittest.main()
