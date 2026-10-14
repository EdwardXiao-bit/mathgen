"""D46 量测工具（verify 用，**不是测试**：文件名不以 test_ 开头，unittest 不收集）。

用途：给出「左到右折叠」的实测取值区间，用来核对 D46 ②③ 的档位上界，
尤其是 D46 点名但契约未定义上界的 **R6**。

    python backend/tests/e2e/measure_d46.py          # 仓库根目录执行
    python measure_d46.py                            # 或在 backend/tests/e2e 下执行

输出示例（8 个 seed × 各档位 count）：
    R1: 64 题  折叠值区间 [0, 20]
    R2: 160 题  折叠值区间 [0, 100]
    R6: 80 题  折叠值区间 [1, 18643]  ...
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(os.path.dirname(_HERE))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.engine import generate                                        # noqa: E402
from app.engine.types import GenerateConfig, Op, RangeId, QuestionType  # noqa: E402
from tests.e2e.harness import fold_sequence                            # noqa: E402

SEEDS = (1, 2, 3, 5, 7, 11, 20261014, 987654321)
STEP = QuestionType.STEPWISE
CASES = (("R1", RangeId.R1, 8), ("R2", RangeId.R2, 20), ("R6", RangeId.R6, 10))
R6_LITERAL_BOUND = 9999        # D16 未定义 R6 上界；9999 = 四位数最大值，仅作参照


def measure(label, range_id, count):
    config = GenerateConfig(ops={Op.ADD, Op.SUB}, ranges=[range_id],
                            question_type=STEP, count=count)
    lowest = highest = None
    questions = 0
    over_bound = []
    for seed in SEEDS:
        for question in generate(config, seed):
            values = [v for v, _remaining in fold_sequence(question.expr)]
            questions += 1
            for value in values:
                lowest = value if lowest is None else min(lowest, value)
                highest = value if highest is None else max(highest, value)
                if value > R6_LITERAL_BOUND:
                    over_bound.append((seed, question.expr, question.steps, value))
    print(f"{label}: {questions} 题  折叠值区间 [{lowest}, {highest}]")
    if over_bound:
        print(f"    超过 {R6_LITERAL_BOUND}（= 四位数最大值）的折叠值：{len(over_bound)} 个，"
              f"涉及 {len({e for _s, e, _st, _v in over_bound})} 道题")
        seed, expr, steps, value = over_bound[0]
        print(f"    例：seed={seed} {expr!r} steps={steps} 越界值={value}")


if __name__ == "__main__":
    for case in CASES:
        measure(*case)
