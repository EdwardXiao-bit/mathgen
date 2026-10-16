"""task-51 判据支撑：仓库源文件必须「纯 LF + 无 BOM」（只读扫描）。

## 这条判据在回答什么

仓库里几乎每份报告的证据段都写着「纯 LF / 无 BOM」，但那是**声明**：
2026-10-16 的一次 CRLF 漂移（`backend/tests/render/pdfprobe.py`，CR=190/LF=190）只有
`git status` 给了个警告，**没有任何判据会因此变红**。本模块把那句话变成机器可判的事实。

## 规则来自仓库自己，不是我的口味

`.gitattributes` 第 2 行写的是 `* text=auto eol=lf`（在 Windows 上也按 LF 存储），
所以「工作区里的源文件必须是 LF」是**仓库的既定策略**，判据只是把它检查出来。

## 只读

本模块只读文件字节，不写任何被扫描的文件。判据要能红，红的时候**点名文件 + 给出 CR 数 /
是否 BOM**，而不是只说「有脏文件」。
"""

from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))          # backend/tests/contract
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))

#: 至少要扫到多少个文件；走到更少（特别是 0）必须红，否则「空集通过」（V11）。
MIN_FILES = 220

#: 判据覆盖的目录（相对仓库根）。范围写死在代码里，避免「扫了哪儿」也变成一句声明。
INCLUDE_ROOTS = (
    "backend/app",
    "backend/tests",
    "frontend/src",
    "frontend/scripts",
    "docs",
)

#: 只看这些后缀。二进制/派生产物靠后缀挡在门外，也靠内容里的 NUL 兜底。
TEXT_SUFFIXES = (
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".scss",
    ".html", ".htm", ".md", ".json", ".yml", ".yaml", ".toml", ".cfg", ".ini",
    ".txt", ".sh", ".ps1", ".sql", ".svg", ".csv",
)

#: 目录名 → 排除理由（理由要和名字一起写下来，见 TestExclusionsAreJustified）。
EXCLUDED_DIRS = {
    ".git": "版本库内部对象，不是源文件",
    ".venv": "venv 里的第三方包不属于本仓库维护的源文件",
    "venv": "同上（备用名）",
    "site-packages": "安装进来的第三方包",
    "node_modules": "前端依赖树，第三方",
    "dist": "构建产物（frontend/dist）",
    "build": "构建产物",
    "artifacts": "测试证据/原始输出（json、日志、截图），不是源文件",
    "__pycache__": "Python 字节码缓存",
    ".pytest_cache": "pytest 缓存",
    ".mypy_cache": "mypy 缓存",
    ".ruff_cache": "ruff 缓存",
    ".pylibs": "vendored 第三方 wheel（pypdfium2 源码本身就是 CRLF，扫它只会制造噪音）",
    ".playwright": "Playwright 浏览器缓存",
    ".idea": "IDE 配置",
    ".vscode": "IDE 配置",
}


def excluded_dir_reason(name: str) -> str | None:
    """目录名是否被排除；返回理由（None = 要扫）。"""
    low = name.lower()
    if low in EXCLUDED_DIRS:
        return EXCLUDED_DIRS[low]
    # 隐藏目录（.pylibs/.playwright/.tmp-*）与 tmp-* 都是工具缓存/第三方/一次性目录：
    # `.pylibs` 里 vendored 的 pypdfium2 就是 CRLF，扫它只会制造噪音。
    if name.startswith("."):
        return "隐藏目录：工具缓存 / vendored 第三方 / 一次性实验（.tmp-*）"
    if low.startswith("tmp-"):
        return "一次性实验目录（tmp-*）"
    return None


def is_text_suffix(name: str) -> bool:
    return name.lower().endswith(TEXT_SUFFIXES)


def inspect_bytes(data: bytes) -> dict:
    """字节级事实：CR 次数、LF 次数、是否 BOM 开头、是否像二进制。"""
    return {
        "cr": data.count(b"\r"),
        "lf": data.count(b"\n"),
        "bom": data.startswith(b"\xef\xbb\xbf"),
        "binary": b"\x00" in data[:8192],
    }


def iter_text_files(root: str, include_roots=INCLUDE_ROOTS) -> list[str]:
    """按范围/后缀/排除项列出候选文件（绝对路径，排序后返回）。"""
    found: list[str] = []
    for root_rel in include_roots:
        base = os.path.join(root, root_rel.replace("/", os.sep))
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if excluded_dir_reason(d) is None]
            for name in sorted(filenames):
                if is_text_suffix(name):
                    found.append(os.path.join(dirpath, name))
    return sorted(found)


def text_hygiene_problems(root: str = REPO, *, min_files: int = MIN_FILES,
                          include_roots=INCLUDE_ROOTS) -> tuple[list[str], dict]:
    """扫描并返回 (problems, observation)。

    problems 里每条都点名具体文件（或具体失明原因）；observation 记录扫了多少、
    跳过了什么，供证据落盘 —— 「扫了 300 个文件都是干净的」这句话必须能自证。
    """
    problems: list[str] = []
    observation: dict = {
        "root": root,
        "include_roots": list(include_roots),
        "files": 0,
        "cr_files": [],
        "bom_files": [],
        "binary_skipped": [],
        "undecodable": [],
        "missing_roots": [],
        "excluded_dir_reasons": {k: v for k, v in EXCLUDED_DIRS.items()},
    }

    if not os.path.isdir(root):
        problems.append(f"扫描根不存在或不是目录: {root}（解析器失明）")
        return problems, observation

    for root_rel in include_roots:
        base = os.path.join(root, root_rel.replace("/", os.sep))
        if not os.path.isdir(base):
            observation["missing_roots"].append(root_rel)
            problems.append(f"应在范围内的目录不存在，判据扫不到它: {root_rel}")

    for path in iter_text_files(root, include_roots):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        with open(path, "rb") as handle:
            data = handle.read()
        facts = inspect_bytes(data)
        if facts["binary"]:
            observation["binary_skipped"].append(rel)
            continue
        observation["files"] += 1
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            # CR/BOM 是字节级事实，非 UTF-8 也照样能查；只是把这件事记下来（§7 边界）。
            observation["undecodable"].append([rel, str(exc)])
        if facts["cr"]:
            observation["cr_files"].append([rel, facts["cr"], facts["lf"]])
            problems.append(
                f"{rel}: CR 出现 {facts['cr']} 次（LF {facts['lf']}）—— 不是纯 LF")
        if facts["bom"]:
            observation["bom_files"].append(rel)
            problems.append(f"{rel}: 以 UTF-8 BOM 开头（EF BB BF）")

    if observation["files"] == 0:
        problems.append(
            "扫描到 0 个文件：空集不许通过（include_roots=%s）" % (list(include_roots),))
    elif observation["files"] < min_files:
        problems.append(
            f"只扫到 {observation['files']} 个文件，少于下限 {min_files}（范围被改小/被吞）")

    observation["cr_files"].sort()
    observation["bom_files"].sort()
    return problems, observation


def summarise(observation: dict) -> str:
    """一行结论，便于写进证据/上报。"""
    return (
        f"files={observation['files']} cr_files={len(observation['cr_files'])} "
        f"bom_files={len(observation['bom_files'])} "
        f"binary_skipped={len(observation['binary_skipped'])} "
        f"undecodable={len(observation['undecodable'])} "
        f"missing_roots={observation['missing_roots']}"
    )
