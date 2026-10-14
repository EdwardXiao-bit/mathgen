# -*- coding: utf-8 -*-
"""生成 mathgen 课程文档所需的 10 张配图。

需求分析：用例图、功能结构图、老师出卷流程图、学生答题流程图
技术设计：系统架构图、类图、数据库 ER 图、卷状态机图、答题判分时序图、部署图
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_kit import C, Canvas  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "word", "figures")


# ---------------------------------------------------------------- 复用组件
def class_box(cv, x, y, w, name, attrs, methods):
    """UML 类框：类名 / 属性 / 方法 三段。返回高度。"""
    hh, rh = 44, 24
    ah = len(attrs) * rh + 16 if attrs else 10
    mh = len(methods) * rh + 16 if methods else 10
    h = hh + ah + mh
    cv.rect(x, y, w, h, fill="#ffffff", outline=C["box_edge"], radius=5)
    cv.rect(x, y, w, hh, fill="#e0e7ff", outline=C["box_edge"], radius=5)
    cv.text(x + w / 2, y + hh / 2, name, size=19, bold=True)
    cv.line([(x, y + hh), (x + w, y + hh)], color=C["box_edge"], width=1.2)
    if methods:
        cv.line([(x, y + hh + ah), (x + w, y + hh + ah)], color=C["box_edge"], width=1.2)
    ty = y + hh + 8 + rh / 2
    for a in attrs:
        cv.text(x + 14, ty, a, size=15, anchor="lm")
        ty += rh
    if methods:
        ty = y + hh + ah + 8 + rh / 2
        for m in methods:
            cv.text(x + 14, ty, m, size=15, anchor="lm")
            ty += rh
    return h


def entity_box(cv, x, y, w, title, fields):
    """ER 实体框：表名 + 字段（可带 PK/FK/UK 标记）。返回高度。"""
    hh, rh = 42, 26
    h = hh + len(fields) * rh + 14
    cv.rect(x, y, w, h, fill="#ffffff", outline=C["alt_edge"], radius=5)
    cv.rect(x, y, w, hh, fill="#d1fae5", outline=C["alt_edge"], radius=5)
    cv.text(x + w / 2, y + hh / 2, title, size=19, bold=True)
    cv.line([(x, y + hh), (x + w, y + hh)], color=C["alt_edge"], width=1.2)
    ty = y + hh + 7 + rh / 2
    for f, tag in fields:
        underline = tag.startswith("PK")
        cv.text(x + 14, ty, f, size=15, anchor="lm")
        if underline:
            tw = cv.textlen(f, 15)
            cv.line([(x + 14, ty + 8), (x + 14 + tw, ty + 8)], color=C["ink"], width=1.2)
        if tag:
            cv.text(x + w - 14, ty, tag, size=13, color="#b45309", anchor="rm")
        ty += rh
    return h


def flow_box(cv, cx, cy, w, h, label, fill=None, outline=None, size=17):
    cv.box(cx - w / 2, cy - h / 2, w, h, label, size=size, fill=fill, outline=outline)


def vseq(cv, prev_cx, prev_bottom, next_cx, next_top, label=None):
    cv.arrow([(prev_cx, prev_bottom + 4), (next_cx, next_top - 6)], label=label, label_size=14)


# ---------------------------------------------------------------- 1 用例图
def fig_usecase():
    cv = Canvas(1900, 1320)
    bx, by, bw, bh = 300, 90, 1300, 1160
    cv.rect(bx, by, bw, bh, fill="#fcfcff", outline=C["band_edge"], radius=10)
    cv.text(bx + bw / 2, by + 36, "小学四则运算练习题生成与批改系统", size=22, bold=True)

    cv.actor(120, 480, "老师")
    cv.actor(1780, 480, "学生")

    t_uc = ["配置出题参数", "生成试卷", "预览与改题", "调整排版参数",
            "输出试卷\n（打印 / PDF / Word）", "发布试卷\n（二维码 + 卷号）",
            "查看成绩与逐题明细", "导出成绩", "管理卷库"]
    t_cy = [210 + i * 118 for i in range(len(t_uc))]
    for name, cy in zip(t_uc, t_cy):
        cv.ellipse(610, cy, 190, 42, name, size=16)
        cv.arrow([(150, 560), (610 - 190 - 6, cy)], width=1.6)

    s_uc = ["身份识别（姓名 + 学号）", "自主练习", "做老师发的卷", "屏幕作答", "查看判分结果"]
    s_cy = [360, 490, 620, 750, 880]
    for name, cy in zip(s_uc, s_cy):
        cv.ellipse(1300, cy, 190, 42, name, size=16)
        cv.arrow([(1750, 560), (1300 + 190 + 6, cy)], width=1.6)

    # «include»：自主练习 / 做老师发的卷 → 屏幕作答（标签单独摆放，避免两处标签重叠）
    cv.arrow([(1110, 490), (1075, 490), (1075, 750), (1110, 750)], dashed=True, width=1.6)
    cv.arrow([(1110, 620), (1015, 620), (1015, 750), (1110, 750)], dashed=True, width=1.6)
    cv.text(1058, 560, "«include»", size=13, anchor="rm")
    cv.text(998, 700, "«include»", size=13, anchor="rm")
    return cv.save(os.path.join(OUT, "fig-usecase.png"))


# ---------------------------------------------------------------- 2 功能结构图
def fig_function_tree():
    cv = Canvas(2000, 800)
    cv.box(690, 36, 620, 68, "小学四则运算练习题生成与批改系统",
           size=20, bold=True, fill="#e0e7ff", outline=C["box_edge"])
    groups = [
        ("老师端", C["box_fill"], C["box_edge"],
         ["出题配置（六维）", "生成与改题", "排版与输出", "发布试卷", "卷库管理", "成绩管理"]),
        ("学生端", C["uc_fill"], C["uc_edge"],
         ["身份识别", "自主练习", "做老师发的卷", "屏幕作答", "结果反馈"]),
        ("出题引擎", C["alt_fill"], C["alt_edge"],
         ["参数模型 R1–R7", "题目构造", "去重与复现", "难度与考点", "过程输出"]),
        ("后期功能", "#f3f4f6", "#6b7280",
         ["手写 OCR 批改", "竖式出题与排版"]),
    ]
    cx_list = [250, 750, 1250, 1750]
    cv.line([(1000, 104), (1000, 140)], color=C["line"], width=2)
    cv.line([(cx_list[0], 140), (cx_list[-1], 140)], color=C["line"], width=2)
    for cx, (name, fill, edge, leaves) in zip(cx_list, groups):
        cv.arrow([(cx, 140), (cx, 152)])
        cv.box(cx - 190, 152, 380, 56, name, size=19, bold=True, fill=fill, outline=edge)
        cv.line([(cx, 208), (cx, 242)], color=C["line"], width=2)
        ly = 242
        for i, leaf in enumerate(leaves):
            cv.line([(cx, ly), (cx, ly + 14)], color=C["line"], width=1.6)
            cv.box(cx - 170, ly + 14, 340, 52, leaf, size=16, fill="#ffffff", outline=edge)
            ly += 72
    return cv.save(os.path.join(OUT, "fig-function-tree.png"))


# ---------------------------------------------------------------- 3 老师出卷流程
def fig_teacher_flow():
    cv = Canvas(1700, 1740)
    cx, w, h = 850, 360, 62
    cv.stadium(cx, 60, 190, 62, "开始")
    nodes = [
        (170, "打开老师端"),
        (280, "设置出题参数（六维）"),
        (390, "生成试卷"),
        (500, "预览卷面"),
    ]
    prev_cx, prev_bottom = cx, 91
    for cy, label in nodes:
        vseq(cv, prev_cx, prev_bottom, cx, cy - h / 2)
        flow_box(cv, cx, cy, w, h, label)
        prev_cx, prev_bottom = cx, cy + h / 2

    cv.arrow([(cx, 531), (cx, 568)])
    cv.diamond(cx, 620, 230, 106, "卷面满意？")
    # 否 → 右侧：删题 / 换一题 → 回到预览
    cv.arrow([(965, 620), (1100, 620)], label="否", label_dy=-18)
    flow_box(cv, 1250, 620, 300, 62, "删题 / 换一题")
    cv.arrow([(1250, 589), (1250, 500), (1036, 500)])
    # 是 → 继续
    cv.arrow([(cx, 673), (cx, 710)], label="是", label_dx=26, label_dy=0)
    steps2 = [
        (760, "调整排版参数（6 项）"),
        (870, "输出试卷（打印 / 导出 PDF / Word）"),
    ]
    for cy, label in steps2:
        flow_box(cv, cx, cy, 470 if cy == 870 else w, h, label)
    cv.arrow([(cx, 791), (cx, 839)])
    cv.arrow([(cx, 901), (cx, 938)])
    cv.diamond(cx, 990, 280, 106, "需要学生在线做？")
    # 是 → 发布
    cv.arrow([(cx, 1043), (cx, 1080)], label="是", label_dx=24, label_dy=0)
    flow_box(cv, cx, 1130, 420, 62, "发布试卷（冻结 + 分配卷号）")
    cv.arrow([(cx, 1161), (cx, 1198)])
    flow_box(cv, cx, 1248, 380, 62, "显示二维码与卷号")
    cv.arrow([(cx, 1279), (cx, 1560)])
    cv.stadium(cx, 1600, 190, 62, "结束")
    # 否 → 左侧长线直接到结束
    cv.arrow([(710, 990), (350, 990), (350, 1600), (749, 1600)], label="否", label_dy=-20)
    return cv.save(os.path.join(OUT, "fig-teacher-flow.png"))


# ---------------------------------------------------------------- 4 学生答题流程
def fig_student_flow():
    cv = Canvas(1400, 1440)
    cx, w, h = 700, 380, 62
    cv.stadium(cx, 60, 190, 62, "开始")
    vseq(cv, cx, 91, cx, 139)
    flow_box(cv, cx, 170, 420, h, "扫码进入 / 输入数字卷号")
    cv.arrow([(cx, 201), (cx, 249)])
    flow_box(cv, cx, 280, 420, h, "输入姓名 + 学号")
    cv.arrow([(cx, 311), (cx, 348)])
    cv.diamond(cx, 400, 300, 110, "选择练习方式")
    # 左右分支
    cv.arrow([(550, 400), (350, 400), (350, 512)], label="自主练习", label_dy=-18, label_size=14)
    cv.arrow([(850, 400), (1050, 400), (1050, 512)], label="老师发的卷", label_dy=-18, label_size=14)
    flow_box(cv, 350, 545, 320, h, "滑杆选择数值范围与题量")
    flow_box(cv, 1050, 545, 320, h, "按卷号加载指定卷")
    cv.arrow([(350, 576), (350, 692), (459, 692)])
    cv.arrow([(1050, 576), (1050, 692), (941, 692)])
    flow_box(cv, cx, 692, 470, h, "生成 / 加载题目（剔除答案字段）")
    cv.arrow([(cx, 723), (cx, 771)])
    flow_box(cv, cx, 802, 470, h, "一屏一题作答（竖屏 · 数字键盘）")
    cv.arrow([(cx, 833), (cx, 881)])
    flow_box(cv, cx, 912, 380, h, "提交作答")
    cv.arrow([(cx, 943), (cx, 991)])
    flow_box(cv, cx, 1022, 380, h, "服务端自动判分")
    cv.arrow([(cx, 1053), (cx, 1101)])
    flow_box(cv, cx, 1132, 380, h, "展示得分与逐题对错")
    cv.arrow([(cx, 1163), (cx, 1211)])
    flow_box(cv, cx, 1242, 420, h, "记录成绩与用时")
    cv.arrow([(cx, 1273), (cx, 1315)])
    cv.stadium(cx, 1348, 190, 62, "结束")
    return cv.save(os.path.join(OUT, "fig-student-flow.png"))


# ---------------------------------------------------------------- 5 系统架构图
def fig_architecture():
    cv = Canvas(1800, 1080)
    bx, bw = 80, 1280

    def band(y, h, title):
        cv.rect(bx, y, bw, h, fill=C["band_fill"], outline=C["band_edge"], radius=8)
        cv.text(bx + 18, y + 20, title, size=17, bold=True, anchor="lm")

    band(70, 122, "浏览器层")
    cv.box(180, 112, 480, 64, "老师端 SPA（/teacher/*）", size=17)
    cv.box(760, 112, 480, 64, "学生端 SPA（/student/*）", size=17)

    band(230, 122, "应用层 · FastAPI + Uvicorn")
    for i, r in enumerate(["routers/papers", "routers/attempts", "routers/students", "routers/exports"]):
        cv.box(130 + i * 310, 262, 285, 58, r, size=15, fill="#ffffff")

    band(390, 192, "领域服务层（纯函数优先，无 I/O）")
    for i, s in enumerate(["generator\n出题", "grader\n判分", "layout\n排版", "renderer\n渲染", "exporter\n导出"]):
        cv.box(116 + i * 248, 438, 228, 68, s, size=15, fill=C["alt_fill"], outline=C["alt_edge"])

    band(620, 112, "仓储层")
    cv.box(600, 652, 440, 56, "SQLAlchemy ORM", size=16, fill="#ffffff")

    band(770, 132, "存储层")
    cv.box(200, 812, 440, 66, "SQLite（WAL）\ndata/mathgen.db", size=15)
    cv.box(760, 812, 440, 66, "导出文件\ndata/exports", size=15)

    for y1, y2 in [(192, 230), (352, 390), (582, 620), (732, 770)]:
        cv.arrow([(720, y1 + 4), (720, y2 - 6)], width=2)

    # 导出侧挂件
    cv.box(1420, 430, 320, 76, "Playwright + Chromium\nPDF 导出", size=15, fill=C["warm_fill"], outline=C["warm_edge"])
    cv.box(1420, 530, 320, 76, "python-docx\nWord 导出", size=15, fill=C["warm_fill"], outline=C["warm_edge"])
    cv.arrow([(1352, 460), (1414, 466)], width=1.8)
    cv.arrow([(1352, 492), (1414, 562)], width=1.8)

    cv.note(80, 940, 1660, 100,
            "单进程部署，uvicorn 绑定 0.0.0.0:8000；前端资源与字体全部本地打包，"
            "不引用任何 CDN —— 完全断网时老师端的出题、打印、导出仍可用（NFR-9）。")
    return cv.save(os.path.join(OUT, "fig-architecture.png"))


# ---------------------------------------------------------------- 6 类图
def fig_class_diagram():
    cv = Canvas(1900, 1250)
    W = 520
    x1, x2, x3 = 60, 690, 1320

    h_q = class_box(cv, x1, 60, W, "Question  «值对象»",
                    ["expr : str", "display : str", "answer : str", "steps : list[str]",
                     "difficulty : int", "tags : list[str]", "signature : str"], [])
    class_box(cv, x2, 60, W, "GenerateConfig  «值对象»",
              ["ops : set[Op]", "ranges : list[RangeId]", "maxValue : int | None",
               "requireCarry : bool", "requireBorrow : bool", "exactDivision : bool",
               "questionType : QuestionType"], [])
    class_box(cv, x3, 60, W, "LayoutParams  «值对象»",
              ["colsPerRow : int", "rowGap : float", "fontSize : int",
               "pageMargin : float", "answerLine : bool", "headerBar : bool"], [])

    h_p = class_box(cv, x2, 400, W, "Paper  «实体»",
                    ["id : int", "code : str | None", "title : str", "status : Status",
                     "seed : int", "config : GenerateConfig", "layout : LayoutParams",
                     "createdAt : datetime", "publishedAt : datetime | None"],
                    ["+ publish() : str", "+ saveAsCopy() : Paper",
                     "+ regenerateItem(seq, seed) : PaperItem", "+ removeItem(seq) : void"])
    class_box(cv, x1, 400, W, "PaperItem  «实体»",
              ["id : int", "paperId : int", "seq : int", "payload : Question", "origin : str"], [])
    class_box(cv, x3, 400, W, "EditLog  «实体»",
              ["id : int", "paperId : int", "action : str", "seq : int", "at : datetime"], [])

    class_box(cv, x1, 820, W, "Student  «实体»",
              ["id : int", "name : str", "studentNo : str", "createdAt : datetime"], [])
    class_box(cv, x2, 820, W, "Attempt  «实体»",
              ["id : int", "studentId : int", "paperId : int | None", "config : GenerateConfig | None",
               "total : int", "correct : int", "durationMs : int", "submittedAt : datetime"], [])
    class_box(cv, x3, 820, W, "AttemptItem  «实体»",
              ["id : int", "attemptId : int", "seq : int", "payload : Question",
               "studentAnswer : str", "isCorrect : bool"], [])

    # 关系连线（多重度标注统一放在连线两侧，避免压进类框）
    cv.arrow([(320, 60 + h_q + 4), (320, 394)], head=False)
    cv.text(334, 312, "1", size=19, bold=True, anchor="lm")
    cv.text(334, 376, "*", size=19, bold=True, anchor="lm")

    cv.arrow([(950, 264 + 4), (950, 394)], head=False)
    cv.text(964, 318, "1", size=19, bold=True, anchor="lm")
    cv.text(964, 378, "*", size=19, bold=True, anchor="lm")

    cv.arrow([(1580, 246 + 4), (1580, 300), (1216, 430)], head=False)
    cv.text(1560, 276, "1", size=18, bold=True, anchor="rm")

    cv.arrow([(1210, 520), (1314, 500)], head=False)
    cv.text(1262, 516, "*", size=20, bold=True)
    cv.text(1296, 470, "1", size=18, bold=True, anchor="rm")

    cv.arrow([(950, 788 + 4), (950, 814)], head=False)
    cv.text(936, 802, "1", size=19, bold=True, anchor="rm")
    cv.text(964, 802, "*", size=19, bold=True, anchor="lm")

    cv.arrow([(580, 890), (684, 890)], head=False)
    cv.text(598, 866, "1", size=19, bold=True)
    cv.text(666, 866, "*", size=19, bold=True)

    cv.arrow([(1210, 890), (1314, 890)], head=False)
    cv.text(1228, 866, "1", size=19, bold=True)
    cv.text(1300, 866, "*", size=19, bold=True)

    cv.note(60, 1130, 430, 60, "Attempt.paperId 为 NULL 表示学生自主练习。", size=15)
    cv.note(690, 1130, 1150, 60,
            "PaperItem.payload 与 AttemptItem.payload 均保存 Question 的完整快照——"
            "卷库读取与历史成绩还原都不重跑引擎（对应 D27 / FR-T14）。", size=15)
    return cv.save(os.path.join(OUT, "fig-class.png"))


# ---------------------------------------------------------------- 7 ER 图
def fig_er():
    cv = Canvas(1900, 900)
    W = 460
    entity_box(cv, 60, 60, W, "paper", [
        ("id", "PK"), ("code", "UK"), ("title", ""), ("status", ""), ("seed", ""),
        ("config_json", ""), ("layout_json", ""), ("created_at", ""), ("published_at", "")])
    entity_box(cv, 700, 60, W, "edit_log", [
        ("id", "PK"), ("paper_id", "FK"), ("action", ""), ("seq", ""), ("at", "")])
    entity_box(cv, 1340, 60, W, "student", [
        ("id", "PK"), ("name", "联合UK"), ("student_no", "联合UK"), ("created_at", "")])

    entity_box(cv, 60, 440, W, "paper_item", [
        ("id", "PK"), ("paper_id", "FK"), ("seq", ""), ("payload", ""), ("origin", "")])
    entity_box(cv, 700, 430, W, "attempt", [
        ("id", "PK"), ("student_id", "FK"), ("paper_id", "FK·可空"), ("config_json", ""),
        ("total", ""), ("correct", ""), ("duration_ms", ""), ("submitted_at", "")])
    entity_box(cv, 1340, 420, W, "attempt_item", [
        ("id", "PK"), ("attempt_id", "FK"), ("seq", ""), ("payload", ""),
        ("student_answer", ""), ("is_correct", "")])

    def rel(a, b, la, lb):
        cv.arrow([a, b], width=1.8)
        cv.text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 - 16, la, size=16, bold=True)
        cv.text(b[0] - (b[0] - a[0]) * 0.18, b[1] - (b[1] - a[1]) * 0.18 - 16, lb,
                size=16, bold=True)

    # paper 1 ── N paper_item（竖线，多重度两侧错开，避免叠字）
    cv.arrow([(290, 350), (290, 432)], width=1.8)
    cv.text(274, 372, "1", size=16, bold=True, anchor="rm")
    cv.text(306, 410, "N", size=16, bold=True, anchor="lm")
    # paper 1 ── N edit_log
    rel((520, 200), (692, 200), "1", "N")
    # paper 1 ── N attempt（直角折线，箭头只出现在终点）
    cv.arrow([(520, 300), (640, 300), (640, 520), (692, 520)], width=1.8)
    cv.text(532, 282, "1", size=16, bold=True, anchor="lm")
    cv.text(650, 430, "N", size=16, bold=True, anchor="lm")
    # student 1 ── N attempt（走上方通道，避开右侧标注密集区）
    cv.arrow([(1570, 220), (1570, 400), (930, 400), (930, 426)], width=1.8)
    cv.text(1552, 300, "1", size=16, bold=True, anchor="rm")
    cv.text(946, 388, "N", size=16, bold=True, anchor="lm")
    # attempt 1 ── N attempt_item
    rel((1160, 560), (1332, 560), "1", "N")

    cv.note(60, 710, 1600, 150,
            "说明：attempt.paper_id 允许为 NULL —— 该次为学生自主练习（无卷）。"
            "attempt_item 冗余保存题目快照，使历史成绩在卷被删除或引擎升级后依然可完整还原（FR-T12）。"
            "student 表以 (name, student_no) 唯一约束区分同名（D35）。", size=16)
    return cv.save(os.path.join(OUT, "fig-er.png"))


# ---------------------------------------------------------------- 8 状态机图
def fig_state():
    cv = Canvas(1500, 780)
    cv.rect(180, 280, 380, 140, fill=C["alt_fill"], outline=C["alt_edge"], radius=10)
    cv.text(370, 320, "draft（草稿）", size=21, bold=True)
    cv.text(370, 356, "可删题 / 可换题", size=16)
    cv.text(370, 386, "无卷号", size=16)

    cv.rect(900, 280, 420, 140, fill=C["box_fill"], outline=C["box_edge"], radius=10)
    cv.text(1110, 320, "published（已发布）", size=21, bold=True)
    cv.text(1110, 356, "只读 · 有卷号", size=16)
    cv.text(1110, 386, "学生可做", size=16)

    cv.d.ellipse([(80 - 11) * 2, (350 - 11) * 2, (80 + 11) * 2, (350 + 11) * 2], fill=C["actor"])
    cv.arrow([(93, 350), (174, 350)], width=2)

    cv.arrow([(562, 350), (894, 350)], width=2.2,
             label="publish()  分配卷号 YYMM-NNNN", label_dy=-20)

    cv.arrow([(250, 278), (250, 210), (490, 210), (490, 278)], width=1.8)
    cv.text(370, 186, "删题 / 换一题", size=16)

    cv.arrow([(1020, 278), (1020, 210), (1200, 210), (1200, 278)], width=1.8)
    cv.text(1110, 186, "学生作答 → 生成 attempt", size=16)

    cv.arrow([(1150, 422), (1150, 570), (400, 570), (400, 424)], dashed=True, width=2,
             label="另存为可编辑副本（得新卷号）", label_dy=-18)

    cv.note(900, 620, 520, 110,
            "发布不可逆：要改题只能另存为新副本。旧卷与其成绩永久保留，"
            "历史成绩永远能对应到当时那份确切的卷面（D32）。", size=15)
    return cv.save(os.path.join(OUT, "fig-state.png"))


# ---------------------------------------------------------------- 9 时序图
def fig_sequence():
    cv = Canvas(1800, 1200)
    lanes = [(150, 160, "学生"), (480, 250, "学生端 SPA"), (860, 250, "«component»\n:AttemptsAPI"),
             (1220, 220, "«component»\n:Grader"), (1580, 220, "«database»\n:SQLite")]
    for x, w, name in lanes:
        cv.box(x - w / 2, 60, w, 62, name, size=16, bold=True,
               fill="#e0e7ff" if x == 150 else C["box_fill"])
        cv.line([(x, 126), (x, 1010)], color=C["band_edge"], width=1.4, dashed=True)

    def msg(y, x1, x2, text, dashed=False, self_loop=False):
        if self_loop:
            cv.arrow([(x1, y), (x1 + 150, y), (x1 + 150, y + 34), (x1 + 8, y + 34)],
                     width=1.8, dashed=dashed)
            cv.text(x1 + 168, y + 16, text, size=15, anchor="lm")
        else:
            cv.arrow([(x1, y), (x2, y)], width=1.8, dashed=dashed)
            cv.text((x1 + x2) / 2, y - 18, text, size=15)

    msg(210, 150, 480, "提交作答")
    msg(290, 480, 860, "POST /api/attempts（answers, durationMs, attempt_uuid）")
    msg(370, 860, 860, "校验 attempt_uuid，重复提交直接返回原结果", self_loop=True)
    msg(470, 860, 1220, "grade(questions, answers)")
    msg(550, 1220, 1220, "答案归一化：全角 → 半角，余数写法统一", self_loop=True)
    msg(650, 1220, 860, "得分 + 逐题对错", dashed=True)
    msg(730, 860, 1580, "INSERT attempt / attempt_item（含题目快照）")
    msg(810, 1580, 860, "落库成功", dashed=True)
    msg(890, 860, 480, "判分结果", dashed=True)
    msg(960, 480, 150, "展示得分与每题对错", dashed=True)

    cv.note(60, 1040, 1680, 120,
            "前置条件：学生端取题接口 GET /api/student/paper/{code} 已剔除 answer 与 steps 字段，"
            "答案不下发到手机端，判分一律在服务端完成——这条边界由自动化测试守住（§13）。", size=16)
    return cv.save(os.path.join(OUT, "fig-sequence.png"))


# ---------------------------------------------------------------- 10 部署图
def fig_deployment():
    cv = Canvas(1700, 780)
    cv.rect(80, 100, 820, 560, fill=C["band_fill"], outline=C["band_edge"], radius=10)
    cv.text(100, 130, "«device»  老师电脑（Windows）", size=19, bold=True, anchor="lm")

    cv.box(120, 180, 340, 72, "浏览器\n老师端 SPA", size=16)
    cv.box(120, 300, 340, 92, "«executionEnvironment»\nuvicorn / FastAPI\n0.0.0.0:8000",
           size=15, fill=C["alt_fill"], outline=C["alt_edge"])
    cv.box(120, 450, 340, 72, "«artifact»\nSQLite  data/mathgen.db", size=15)
    cv.box(510, 300, 350, 72, "Playwright + Chromium\n（PDF 导出）", size=15,
           fill=C["warm_fill"], outline=C["warm_edge"])
    cv.box(510, 450, 350, 72, "«artifact»\n导出文件 data/exports", size=15)

    cv.arrow([(290, 252), (290, 294)], label="HTTP", label_dx=34, label_dy=0, label_size=14)
    cv.arrow([(290, 392), (290, 444)], label="SQL", label_dx=30, label_dy=0, label_size=14)
    cv.arrow([(460, 330), (504, 330)], label="HTML", label_dy=-16, label_size=14)
    cv.arrow([(460, 360), (504, 470)], label="", label_size=14)

    cv.rect(1080, 100, 540, 330, fill=C["band_fill"], outline=C["band_edge"], radius=10)
    cv.text(1100, 130, "«device»  学生手机", size=19, bold=True, anchor="lm")
    cv.box(1140, 170, 420, 90, "移动浏览器\n学生端 SPA", size=17)
    cv.box(1140, 290, 420, 110, "扫码进入 / 输入卷号\n输入姓名 + 学号", size=15,
           fill=C["uc_fill"], outline=C["uc_edge"])

    cv.arrow([(1074, 300), (906, 340)], both=True, width=2)
    cv.text(990, 236, "«protocol» HTTP", size=14)
    cv.text(990, 258, "局域网 Wi-Fi", size=14)

    cv.note(1080, 470, 540, 190,
            "不依赖公网、不需要域名与备案。\n"
            "服务启动时终端打印局域网访问地址与二维码，学生扫码即可进入；"
            "老师端「发卷」页同时显示二维码与数字卷号（D23 / NFR-1）。", size=16)
    return cv.save(os.path.join(OUT, "fig-deployment.png"))


def main():
    jobs = [fig_usecase, fig_function_tree, fig_teacher_flow, fig_student_flow,
            fig_architecture, fig_class_diagram, fig_er, fig_state,
            fig_sequence, fig_deployment]
    for job in jobs:
        path = job()
        print("OK", os.path.basename(path), os.path.getsize(path) // 1024, "KB")


if __name__ == "__main__":
    main()
