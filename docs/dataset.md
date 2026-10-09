# 数据集描述

本数据集包含膝关节 MRI 检查，并标注了十二种常见病变：韧带和半月板损伤、骨关节炎的三个腔室、关节积液、滑膜炎、贝克囊肿、骨挫伤和骨折。每项检查均包含来自单次扫描的多个 MRI 序列，格式为 DICOM 序列。任务是预测每项检查中出现这十二种病变的概率。这些研究来自世界各地不同的影像中心，涵盖了各种扫描仪、检查方案和人群。只有一小部分训练研究带有针对特定病症的标签。此外，还提供放射学报告的原始文本，可以从中获取其余研究的标签。

数据集文件被 `.gitignore` 限制在本地。需注意，刚 clone 本仓库时，仓库中没有数据集文件，数据集相关文件需通过 [Kaggle CLI](`docs/background/kaggle_cli.md`) 下载至 `.env` 规定的 `RAW_DIR` 中。

## 文件

本数据集含多来源的 819640 个 `.dcm` `.csv` 文件，共 569.76 GB。

1. `train.csv`，每行代表一个训练研究。
  - `StudyInstanceUID`: 研究的唯一标识符；与文件夹名称匹配 `train_series/`。
  - `Report`: 放射学报告的自由文本。可能使用多种语言，具体取决于出具报告的机构。

十二个二进制标签：

`ACL` - 前交叉韧带损伤（0/1）。
`MCL` - 内侧副韧带损伤（0/1）。
`Medial Meniscus` - 内侧半月板撕裂（0/1）。
`Lateral Meniscus` - 外侧半月板撕裂（0/1）。
`Medial OA` - 内侧胫股关节间室骨关节炎（0/1）。
`Lateral OA` - 外侧胫股关节间室骨关节炎（0/1）。
`PF OA` - 髌股关节骨关节炎（0/1）。
`Effusion` - 关节积液/液体过多（0/1）。
`Synovitis` - 关节内膜炎症（0/1）。
`Baker's` - 贝克氏囊肿（0/1）。
`Contusion` - 骨挫伤/骨挫伤（0/1）。
`Fracture` - 骨折（0/1）。

2. `train_series.csv` 文件中，每行代表一个训练序列。每个序列代表一次 MRI 采集，每个研究包含多个序列。

`StudyInstanceUID` - 研究本系列所属的系列。
`SeriesInstanceUID` - 系列的唯一标识符；与文件夹名称匹配 `train_series/<StudyInstanceUID>/`。
`Fluid_Sensitive` - 如果序列强调液体信号（T2、PD、STIR 等），值为 `1`，`0` 则相反。
`Fat_Suppression` - 如果序列应用了脂肪抑制，值取 `1`；否则为零。请注意，虽然如训练集中所观察到的，`Fluid_Sensitive` 与 `Fat_Suppression` 通常相关，但它们并非在所有情况下都等价。
`Anatomical_Plane` - 成像平面：`Sagittal，`Coronal` 或 `Axial`。

3. `train_series/`：训练 DICOM 数据集，按 `train_series/<StudyInstanceUID>/<SeriesInstanceUID>/<SOPInstanceUID>.dcm` 组织。每个 `.dcm` 数据集代表一个图像切片。序列通常包含 20-45 个切片（中位数为 30 个），并有长尾，最长可达数百个切片。

4. `test.csv` 是一个示例测试文件，其中包含来自公开测试集的三个研究 ID。在评分过程中，此示例数据将被实际测试数据替换。测试集包含约 1300 项研究。测试阶段不会提供 `Report` 字段。

`StudyInstanceUID` - 测试集研究的唯一标识符。

5. `test_series.csv` 测试序列，与 `train_series.csv` 的模式相同。在评分过程中替换为实际的测试系列描述符。

6. `test_series/` 示例测试的 DICOM 文件，布局与 `train_series/` 相同。评分期间替换为实际测试集 DICOM。

7. `sample_submission.csv` 是一个有效的提交文件，其中所有标签列都设置为 `0.5`。

## 数据集分发通知
尽管已尽力确保每个数据集都包含每种异常情况，但不能保证训练集、公开排行榜和最终评估数据集中的异常情况发生率相同。

## DICOM 注释
不同系列和研究中的强度、方向和分辨率各不相同。系列采用多种传输语法（未压缩的显式 VR 小端序、JPEG 无损、JPEG 2000、隐式 VR 小端序）。每个 DICOM 文件都已精简至仅包含 86 个元数据标签的允许列表。