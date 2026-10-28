# mathgen-cli

小学数学老师用的**出题 / 打印 / 批改**工具 `mathgen` 的 **npm 启动器** —— 一条命令在本机拉起老师端与学生端。
不需要注册、不需要联网、不需要服务器。

```sh
npx mathgen-cli@rc          # 或写死版本：npx mathgen-cli@1.0.0-rc3
```

> **当前是预发布版（release candidate）**：npm 上 `rc` 与 `latest` 都指向 `1.0.0-rc3`，
> 所以 `@rc`、`@1.0.0-rc3` 与裸写 `npx mathgen-cli` 拿到的是同一份；要钉住版本就写死版本号。
>
> **包名为什么不是 `mathgen`？** npm 上那个名字被判定与已有的 `math-gen` 太像（去标点后同名），
> `npm publish` 直接 403。包名改成 `mathgen-cli`，但里面的命令仍然是 `mathgen`，
> 用法与仓库 README 里写的完全一样。

跑起来后浏览器打开 <http://127.0.0.1:8000/teacher>。

> 这个包是**启动器**，不是把后端用 JS 重写了一遍。它会：
> ① 找一个 Python 3.12+ → ② 在 `~/.mathgen/npm-<版本>/venv` 建一个**独立**虚拟环境
> → ③ 从 [GitHub Release](https://github.com/EdwardXiao-bit/mathgen/releases) 下载并安装后端 wheel
> （**wheel 里自带前端产物**）→ ④ 需要时下 Chromium（PDF 导出用）→ ⑤ 启动服务。
>
> 所以它**需要本机有 Python 3.12+**：Python 应用没法在 Node 里跑。
> 不想装 Python 就用 Docker 方式，见仓库 README。

## 用法

```sh
npx mathgen-cli@rc                             # 默认 127.0.0.1:8000，自动打开浏览器
npx mathgen-cli@rc --port 9000                 # 换端口
npx mathgen-cli@rc --host 0.0.0.0              # 让同一局域网的学生也能连（教室场景）
npx mathgen-cli@rc --data-dir D:\math          # 数据目录
npx mathgen-cli@rc --no-browser                # 不要自动开浏览器
```

参数会**原样转交**给 `mathgen` 命令，完整列表：

```
--host HOST        监听地址，默认 127.0.0.1
--port PORT        端口，默认 8000
--data-dir DIR     数据目录，默认 ~/.mathgen/data
--db PATH          直接指定数据库文件
--static-dir DIR   前端产物目录
--no-browser       启动后不要自动打开浏览器
--version          打印版本号
```

## 它做了什么、没做什么

**做**：把 Python 环境和后端装到 `~/.mathgen/npm-<版本>` 下（删掉这个目录就干净了），
然后把参数转交给它。不会动你的系统 Python，不需要管理员权限，不写全局配置。

**不做**：不常驻后台、不开机自启、不对外暴露——它是给「教室一台电脑」用的。

## 数据在哪

`~/.mathgen/data/mathgen.db`（一个 SQLite 文件）。备份就是拷这个文件。
用 `--data-dir` 可以换地方。

## 常见问题

**PDF 导出报 503？** Chromium 没装好。`npx mathgen-cli@rc` 首次会自己装；
如果当时网络不通，手动补一次：

```sh
# 用启动器建出来的那个环境
~/.mathgen/npm-1.0.0-rc3/venv/bin/python -m playwright install chromium
```

（Windows 上是 `%USERPROFILE%\.mathgen\npm-1.0.0-rc3\venv\Scripts\python.exe`）

**下载 wheel 失败？** 可以指定别的下载地址或本地文件：

```sh
MATHGEN_WHEEL_URL=https://你的镜像/mathgen_backend-1.0.0rc3-py3-none-any.whl npx mathgen-cli@rc
MATHGEN_WHEEL_PATH=D:\build\mathgen_backend-1.0.0rc3-py3-none-any.whl npx mathgen-cli@rc
```

**想彻底卸载？** 删掉 `~/.mathgen` 即可。

## 链接

- 仓库与完整文档：<https://github.com/EdwardXiao-bit/mathgen>
- 问题反馈：<https://github.com/EdwardXiao-bit/mathgen/issues>

## 许可

MIT © 2026 肖铭皓、胡宇书
