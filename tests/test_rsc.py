"""``.rsc`` 解析测试：重复键、真实工程文件、动作帧。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from romanbo import rsc  # noqa: E402
from romanbo.rsc import RscFormatError, RscProject  # noqa: E402

#: 随仓库分发的样例工程（由 rsc.dumps_project 生成，见 examples/data/）
#: parents[1] = 仓库根（tests/ 的上一级）
SAMPLE_RSC = (Path(__file__).resolve().parents[1]
              / "examples" / "data" / "demo.rsc")


class TestDumpsProject(unittest.TestCase):
    """示教导出：生成的文本必须能被解析回去（往返一致）。"""

    @staticmethod
    def _frames() -> list:
        pose_a = [512] * 17
        pose_b = [512] * 7 + [494, 512, 774] + [512] * 7
        return [{"adc": pose_a, "period": 500},
                {"adc": pose_b, "period": 300, "scene": 1}]

    def test_round_trip(self) -> None:
        text = rsc.dumps_project(self._frames(), project_name="taught.rsc",
                                 scene_prefix="taught")
        # 关键：必须是「重复键对象」而不是数组，否则上位机无法识别
        self.assertIn('"ADC": 494,', text)
        self.assertNotIn('"ADC": [', text)
        project = RscProject.loads(text)
        frames = list(project.frames())
        self.assertEqual(len(frames), 2)
        self.assertEqual(project.motor_count, 17)
        self.assertEqual(frames[0].adc, tuple([512] * 17))
        self.assertEqual(frames[1].adc[7], 494)
        self.assertEqual(frames[1].adc[9], 774)
        self.assertEqual(frames[1].period_ms, 300)
        self.assertEqual([f.scene_index for f in frames], [0, 1])

    def test_rejects_wrong_channel_count(self) -> None:
        with self.assertRaises(RscFormatError):
            rsc.dumps_project([{"adc": [512] * 3}])

    def test_rejects_empty(self) -> None:
        with self.assertRaises(RscFormatError):
            rsc.dumps_project([])

    def test_write_and_reload(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "taught.rsc"
            rsc.write_project(path, self._frames())
            project = RscProject.load(str(path))
            self.assertEqual(project.project_name, "taught.rsc")
            self.assertEqual(len(list(project.frames())), 2)


class TestRepeatedKeys(unittest.TestCase):
    def test_duplicate_keys_become_list(self) -> None:
        text = ('{"Header": {"Type": 1}, "Body": {'
                '"Motion": [{"SceneNumber": 0, "MotionNumber": 0, "Period": 500,'
                '"MotorValue": {"ADC": 512, "ADC": 563, "ADC": 205},'
                '"LEDValue": {"LED": 0, "LED": 1, "LED": 2}}]}}')
        project = RscProject.loads(text)
        frames = list(project.frames())
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].adc, (512, 563, 205))
        self.assertEqual(frames[0].led, (0, 1, 2))
        self.assertEqual(frames[0].period_ms, 500)

    def test_missing_header_raises(self) -> None:
        with self.assertRaises(RscFormatError):
            RscProject.loads('{"foo": 1}')
        with self.assertRaises(RscFormatError):
            RscProject.loads("not json")


class TestShippedSample(unittest.TestCase):
    """随仓库分发的 ``examples/data/demo.rsc``。

    该文件由 :func:`romanbo.rsc.dumps_project` 生成并入库，保证 ``.rsc``
    读取链路始终有回归覆盖，不依赖仓库外的样例文件。
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not SAMPLE_RSC.is_file():
            raise unittest.SkipTest(f"缺少样例文件 {SAMPLE_RSC}")
        cls.project = RscProject.load(SAMPLE_RSC)

    def test_header(self) -> None:
        self.assertEqual(self.project.motor_count, 17)
        self.assertEqual(self.project.type_id, 1)
        self.assertEqual(len(self.project.dial_position), 17)
        self.assertEqual(len(self.project.motor_state), 17)
        self.assertEqual(self.project.motor_state[0]["adc"], 512)

    def test_scenes_and_frames(self) -> None:
        scenes = self.project.scenes
        self.assertEqual([s.index for s in scenes], [0, 1])
        self.assertEqual(scenes[0].name, "demo_1")
        frames = list(self.project.frames())
        self.assertEqual(len(frames), 3)
        self.assertEqual(len(frames), sum(s.motion_count for s in scenes))
        first = frames[0]
        self.assertEqual(first.motor_count, 17)
        self.assertEqual(first.adc[:3], (512, 563, 205))
        self.assertEqual(first.period_ms, 500)

    def test_scene_filter(self) -> None:
        frames = list(self.project.frames(scene=1))
        self.assertEqual(len(frames), 1)
        self.assertTrue(all(f.scene_index == 1 for f in frames))

    def test_targets_mapping(self) -> None:
        first = next(self.project.frames())
        targets = first.targets(id_offset=1)
        self.assertEqual(targets[1], 512)
        self.assertEqual(targets[2], 563)
        self.assertEqual(targets[3], 205)
        self.assertEqual(len(targets), 17)

    def test_summary(self) -> None:
        summary = self.project.summary()
        self.assertEqual(summary["motor_count"], 17)
        self.assertEqual(summary["frame_count"], 3)
        self.assertEqual([s["name"] for s in summary["scenes"]],
                         ["demo_1", "demo_2"])

    def test_rewrite_round_trip(self) -> None:
        # 读出来再写一遍，应能被再次解析出完全相同的动作帧
        frames = [{"adc": list(f.adc), "period": f.period_ms, "scene": f.scene_index}
                  for f in self.project.frames()]
        text = rsc.dumps_project(frames, motor_count=self.project.motor_count)
        again = list(RscProject.loads(text).frames())
        self.assertEqual([f.adc for f in again],
                         [f.adc for f in self.project.frames()])


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
