# mathgen 后端

小学四则运算练习题生成与批改系统的服务端：出题引擎、判分、A4 排版与容量守卫、
PDF / Word 导出、REST API 与静态页托管。

这个包同时是一个可运行的命令行程序：

```bash
pip install mathgen_backend-1.0.0rc3-py3-none-any.whl
python -m playwright install chromium   # 只在需要 PDF 导出时
mathgen                                  # 或 python -m app
```

然后浏览器打开 <http://127.0.0.1:8000/teacher>。

**完整说明（四种安装方式、老师/学生使用流程、常见问题、已知限制）请看仓库根目录的
[README.md](https://github.com/EdwardXiao-bit/mathgen#readme)。**

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--host` / `--port` | 监听地址与端口（默认 `127.0.0.1:8000`） |
| `--db PATH` | 指定数据库文件 |
| `--data-dir DIR` | 指定数据目录（默认 `~/.mathgen/data`） |
| `--no-browser` | 不自动打开浏览器 |
| `--version` | 打印版本 |

开发（从源码跑测试、改前端）见仓库根 README 的「从源码开发」一节。
