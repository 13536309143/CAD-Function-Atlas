# 按功能整理 CAD 模型

当前入口为 `run_function_pilot.py`。它输出功能大类、功能描述、次要功能和复核状态，不再要求识别具体零件种类。

本轮实测已完成：**10件模型、累计30次API请求（含1次格式修复），2件自动接受、8件待复核，无最终处理失败项。** 预设目录功能参考匹配7/10；这不是人工确认的实际用途准确率。25项测试通过，10件整理副本与原文件SHA-256全部一致。

## 初始功能体系

| 代码 | 功能类型 | 整理依据 |
|---|---|---|
| transmission_drive | 动力与传动 | 产生或传递动力、扭矩和运动 |
| support_guidance | 支承与导向 | 承受运动载荷，引导相对运动或减摩 |
| connection_fastening | 连接与紧固 | 将构件连接、夹持或锁紧 |
| positioning_spacing | 定位限位与间隔 | 确定位置、限制位移、维持间距或分散接触载荷 |
| sealing_closure | 密封与封堵 | 封闭开口、阻隔介质或防止泄漏 |
| elastic_buffering | 弹性储能与缓冲 | 弹性储能、复位、吸振与缓冲 |
| fluid_control | 流体输送与控制 | 构成、连接或调节流体通道 |
| operation_control | 操作与控制 | 接受操作，提供控制或检测输入 |
| structural_support | 结构承载与防护 | 提供固定承载、安装骨架或防护 |
| unknown | 待确认功能 | 单件形状不足以确定主要作用 |

这是本轮试验采用的自定义整理体系，不是对每个模型实际用途的保证。多个功能均可能成立时，记录一个主要功能与最多两个次要功能；缺少装配信息会改变主要归类时保持待复核。

## 小量试验

默认从10个原目录各抽1件，以覆盖不同功能形态。保留之前部分试验模型作为对照，但直接重新调用视觉API判断功能，不把此前具体名称机械映射成新预测。采样 seed 默认42。

```powershell
.\.venv\Scripts\python.exe run_function_pilot.py --max-calls 31

# 自选来源目录只是为了取样，不会把目录标签发送给分类agent
.\.venv\Scripts\python.exe run_function_pilot.py --sources gear spring valve --per-source 2 --max-calls 19

# 离线只检查处理和图像，不调用API、不复制归类模型
.\.venv\Scripts\python.exe run_function_pilot.py --offline
```

基础预算为1次真实图像探针、每件2次独立功能判断，有分歧或证据不足时再进行1次补充视角裁决，即 `1 + 3 × 样本数`。API错误不自动重试；输出结构不合规时最多增加一次修复请求，该请求也消耗同一个总预算，原始错误响应会保留。若所有样本均触发裁决和修复，预算可能不足，届时明确标记失败，不突破预算。密钥继续使用本地 `.env`。

当前最终试验目录：[outputs/function_pilot_final](outputs/function_pilot_final/)。查看：

- [report.md](outputs/function_pilot_final/report.md)：功能大类和结果概览。
- [classification.csv](outputs/function_pilot_final/classification.csv)：便于筛选的功能分类清单，UTF-8 BOM编码。
- [gallery.html](outputs/function_pilot_final/gallery.html)：查看模型多视图与功能依据。
- [organized_models](outputs/function_pilot_final/organized_models/)：只整理本轮选中的模型副本。
- [review_queue.json](outputs/function_pilot_final/review_queue.json)：记录人工确认功能的入口。

首次执行记录在 `outputs/function_pilot_v1`，其中一件裁决响应的JSON末尾截断。恢复执行复用了已经完成的两个独立判断，只补充1次结构修复请求；原始响应、修复请求以及修复前结果均保留在最终目录。累计请求数30低于31次预算。

```powershell
# 将未完成的结构化输出恢复到新目录，保留原运行证据；不重新调用已完成的agent
.\.venv\Scripts\python.exe recover_function_pilot.py --run outputs\function_pilot_v1 --out outputs\new_recovered_run
```

恢复任务沿用原运行的剩余请求预算。每个角色只允许一次结构修复；HTTP/API失败和几何处理失败不由此工具自动重试。

整理副本按 `organized_models/自动接受或待复核/功能代码_中文名/sample_ID.obj` 分区，未知功能另放待确认区。复制前后核对SHA-256；原始 `data` 不移动、不改名。自动接受表示两项独立判断满足试验阈值和校验规则，并不表示用途已被人工确认。

## 验证方式

功能体系及目录到功能的参考集合会在API调用前保存。agent只收到匿名多视图、几何测量和功能定义，不知道来源目录、旧分类结果或参考功能集合。

目录参考仅用于粗略检查。有些原目录可能对应多个功能，有些通用形状目录无法定义可靠参考；这与实际装配用途可能不同。因此报告使用“参考匹配率”，**人工确认之前不报告真实功能准确率**。这项任务与此前具体零件名称分类不同，不能直接比较两个任务的分数来声称精度提升。

下一步可根据本次图像和清单确认这些大类是否适合自己的模型库，再增加每类样本量。大规模整理应先冻结体系，并核对待确认样本的装配背景。
