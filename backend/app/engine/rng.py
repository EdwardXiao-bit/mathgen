"""确定性伪随机数发生器。

为什么不用标准库 `random`：
  - 契约要求「同 (config, seed) 必得逐题相同的结果」（FR-E8，冻结）。
    自带的 SplitMix64 实现把「确定性」钉死在本仓库里，不依赖任何
    Python 版本对 `random` 内部算法的实现细节，也不碰全局可变状态。

纯函数纪律：本模块只用整数运算，无 I/O（契约 §8）。
"""
from __future__ import annotations

__all__ = ["Rng", "mix_seed"]

_MASK64 = (1 << 64) - 1
_GOLDEN = 0x9E3779B97F4A7C15
_MUL1 = 0xBF58476D1CE4E5B9
_MUL2 = 0x94D049BB133111EB
_FNV = 0x100000001B3


def mix_seed(*parts: int) -> int:
    """把若干整数混成一个 64 位种子（顺序敏感、跨平台一致）。

    负数与超出 64 位的输入都会被掩码折叠，因此 `seed=-1` 也是合法输入。
    """
    h = _GOLDEN
    for part in parts:
        h ^= int(part) & _MASK64
        h = (h * _FNV) & _MASK64
        h ^= h >> 29
    return h & _MASK64


class Rng:
    """SplitMix64 随机源（无全局状态，实例之间互不影响）。"""

    __slots__ = ("_state",)

    def __init__(self, seed: int) -> None:
        self._state = mix_seed(seed)

    def next_u64(self) -> int:
        """产生下一个 64 位无符号整数。"""
        self._state = (self._state + _GOLDEN) & _MASK64
        z = self._state
        z = ((z ^ (z >> 30)) * _MUL1) & _MASK64
        z = ((z ^ (z >> 27)) * _MUL2) & _MASK64
        return (z ^ (z >> 31)) & _MASK64

    def randint(self, lo: int, hi: int) -> int:
        """闭区间 [lo, hi] 上的整数；区间为空时抛 ValueError。"""
        if lo > hi:
            raise ValueError("randint: empty range [%d, %d]" % (lo, hi))
        return lo + (self.next_u64() % (hi - lo + 1))

    def choice(self, items):
        """从非空序列中等概率取一个元素。"""
        seq = list(items)
        if not seq:
            raise ValueError("choice: empty sequence")
        return seq[self.next_u64() % len(seq)]

    def shuffle(self, items):
        """返回打乱后的新列表（Fisher-Yates），不修改入参。"""
        seq = list(items)
        for i in range(len(seq) - 1, 0, -1):
            j = self.next_u64() % (i + 1)
            seq[i], seq[j] = seq[j], seq[i]
        return seq
