"""task-50：测试设施的「服务端输出去哪里」判据。

修的是 `backend/tests/_uvicorn_boot.py` 起子进程时把 stdout/stderr 塞进**没人读**的管道
（Windows 匿名管道约 4KB），服务端一次异常 traceback（实测 8697 字节）就会让它卡在写日志上，
观察者看到「客户端超时 + 日志为空」——真实故障被伪装成环境问题。

* `fixture_app.py`：合成 ASGI 应用，可控地产生 >4KB 输出（不进产品镜像）。
* `artifacts/task50_pipeline_probe.py`：复现探针（原始证据生成器，不被 unittest 收集）。
* `test_server_log_pipeline.py`：留仓库的判据（旧世界必须红、新世界必须绿）。
"""
