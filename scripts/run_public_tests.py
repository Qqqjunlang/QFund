"""公开离线测试入口；不加载依赖个人录制及行情的私有衔接模块。"""
from pathlib import Path
import sys
import unittest


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / 'tests'))
    sys.path.insert(0, str(root / 'src'))
    private = 'test_stage_handoff'
    names = [p.stem for p in sorted((root / 'tests').glob('test_*.py')) if p.stem != private]
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    print('Public offline modules:', len(names), flush=True)
    print('Excluded private recorded integration module:', private, flush=True)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
