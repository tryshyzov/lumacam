import tempfile
import unittest
from pathlib import Path, PureWindowsPath

import av
import numpy as np
from PIL import Image

from camera_studio import look
from camera_studio.look import Look, LookFilter, bake, combined, read_cube, write_cube


def identity(size: int = 5) -> np.ndarray:
    steps = np.linspace(0, 1, size)
    blue, green, red = np.meshgrid(steps, steps, steps, indexing="ij")
    return np.stack([red, green, blue], axis=-1)


class CubeTests(unittest.TestCase):
    def test_reads_back_what_it_writes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "x.cube"
            write_cube(identity(), path)
            np.testing.assert_allclose(read_cube(path), identity(), atol=1e-6)

    def test_red_changes_fastest_in_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "red.cube"
            # Every output pure red: a LUT that maps anything to red.
            path.write_text("LUT_3D_SIZE 2\n" + "1 0 0\n" * 8)
            table = read_cube(path)
            self.assertEqual(table.shape, (2, 2, 2, 3))
            np.testing.assert_allclose(table[1, 1, 1], [1, 0, 0])

    def test_rejects_what_is_not_a_3d_lut(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            for text in ("LUT_1D_SIZE 4\n0 0 0\n", "hello\n", "LUT_3D_SIZE 2\n0 0 0\n"):
                path = Path(folder) / "bad.cube"
                path.write_text(text)
                with self.assertRaises(ValueError):
                    read_cube(path)


class LookTests(unittest.TestCase):
    def test_a_neutral_look_is_the_identity(self) -> None:
        self.assertTrue(Look().neutral)
        np.testing.assert_allclose(bake(Look(), size=5), identity(), atol=1e-9)

    def test_mono_makes_every_colour_grey(self) -> None:
        table = bake(look.PRESETS["mono"], size=5)
        np.testing.assert_allclose(table[..., 0], table[..., 1], atol=1e-9)
        np.testing.assert_allclose(table[..., 1], table[..., 2], atol=1e-9)

    def test_warmth_adds_red_and_takes_blue(self) -> None:
        grey = bake(Look(warmth=0.5), size=5)[2, 2, 2]
        self.assertGreater(grey[0], 0.5)
        self.assertLess(grey[2], 0.5)

    def test_the_users_lut_goes_under_the_sliders(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "red.cube"
            path.write_text("LUT_3D_SIZE 2\n" + "1 0 0\n" * 8)
            table = bake(combined("none", Look(lut=str(path))), size=3)
            np.testing.assert_allclose(table[1, 1, 1], [1, 0, 0], atol=1e-9)

    def test_presets_combine_with_the_sliders(self) -> None:
        mixed = combined("warm", Look(warmth=0.1, saturation=-5))
        self.assertAlmostEqual(mixed.warmth, look.PRESETS["warm"].warmth + 0.1)
        self.assertEqual(mixed.saturation, -1.0)


class FilterTests(unittest.TestCase):
    def frame(self) -> av.VideoFrame:
        image = Image.new("RGB", (64, 36), (120, 80, 200))
        return av.VideoFrame.from_image(image).reformat(format="yuv420p")

    def test_a_neutral_look_passes_frames_through(self) -> None:
        frame = self.frame()
        self.assertIs(LookFilter().apply(frame), frame)

    def test_applies_a_look_to_decoded_frames(self) -> None:
        filtering = LookFilter()
        filtering.set_look(look.PRESETS["mono"])
        for _ in range(3):
            out = filtering.apply(self.frame())
        r, g, b = out.to_image().getpixel((32, 18))
        self.assertLessEqual(max(r, g, b) - min(r, g, b), 3)

    def test_windows_paths_are_escaped_for_ffmpeg(self) -> None:
        path = look.filter_path(Path(PureWindowsPath(r"C:\Users\me\look.cube").as_posix()))
        self.assertEqual(path, r"C\:/Users/me/look.cube")


if __name__ == "__main__":
    unittest.main()
