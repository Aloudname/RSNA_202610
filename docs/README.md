# Kaggle RSNA 膝关节异常检测

## 项目描述

[来自 Kaggle 社区]

一次膝关节扫描就能发现十几种不同的问题。本次竞赛的任务是构建机器学习模型，用于检测膝关节核磁共振检查中一组特定的、具有临床意义的异常情况。

膝关节是人体最容易受伤和接受影像检查的关节。仅骨关节炎一项就影响着全球约6.54亿人，而急性膝关节损伤占所有运动相关创伤的15%至40%。磁共振成像（MRI）能够清晰地显示韧带、软骨、半月板和骨骼的细节，且无需使患者暴露于辐射。

解读这些扫描结果并非总是易事。前交叉韧带和内侧副韧带撕裂、半月板损伤、软骨缺损、骨折以及其他异常情况可能较为隐匿，放射科医生对它们的解读也并非总是一致。此外，肌肉骨骼放射科医生的资源也较为有限，尤其是在大型医疗中心以外的地区，这会导致诊断延误和结果不一致。

本次竞赛中，你将开发多模态机器学习模型，用于检测十二种具有临床意义的膝关节异常。你将使用首个RSNA AI挑战赛数据集，该数据集将每项影像学检查与其原始放射学报告进行匹配，使你的模型能够同时从图像扫描和诊断文本中学习。

高性能模型可以作为强大的决策支持工具，提供所需的准确性、一致性和速度，从而提升专家级膝关节 MRI 解读水平，并改善不同临床环境中的护理。

## 评估方法

[来源](www.kaggle.com/competitions/rsna-knee-abnormality-detection/overview/evaluation)

提交结果的评估依据是预测置信度得分与十二个目标中观察到的目标值之间的ROC 曲线下面积的平均值：

$$
\text{Final Score} = \frac{1}{12}\sum_{i=0}^{11} AUC_i
$$

换句话说，最终得分是宏观平均 AUC ROC。

## 提交文件

对于测试集中的每一行，必须预测十二个目标标签中每个标签的置信度得分。文件应包含标题，并采用以下格式：

```
StudyInstanceUID,ACL,MCL,Medial Meniscus,Lateral Meniscus,Medial OA,Lateral OA,PF OA,Effusion,Synovitis,Baker's,Contusion,Fracture
<uid_1>,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5
<uid_2>,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5
...
```

## DDL

- 2026年7月30日- 开始日期。

- 2026年10月15日 报名截止日期。您必须在此日期前接受比赛规则方可参赛。

- 2026年10月15日 团队合并截止日期。这是参与者加入或合并团队的最后一天。

- 2026年10月22日 最终提交截止日期。

- 2026年11月5日 获奖者提交材料截止日期。获奖者需在此日期前向主办方/Kaggle提交其训练代码、视频和方法描述。

除非另有说明，所有截止时间均为相应日期的 UTC 时间晚上11:59。比赛组织方保留在必要时更新比赛时间表的权利。

## 注意事项

请注意，根据比赛规则，除了标准的 Kaggle 优胜者义务（开源许可要求、解决方案打包/交付、向主办方展示）之外，主办方团队还要求：

（i）制作一段简短的视频，介绍你的方法和解决方案，以及

（二）在竞赛论坛上发布你的开源代码链接和权重。

（三）将模型的最终版本公开发布，以便开放分发和验证。请参考https://www.kaggle.com/models/tom99763/9th-place-models-rsna-iad/PyTorch/default作为示例。

## 代码要求

本次比赛的作品必须通过 Notebooks 提交。提交后，“提交” 按钮必须满足以下条件才能激活：

CPU 笔记本电脑运行时间 ≤ 9小时
GPU 笔记本电脑续航时间 ≤ 9小时
互联网访问已禁用
允许使用免费且公开的外部数据，包括预训练模型。
提交的文件必须命名 `submission.csv`

请参阅代码竞赛常见问题解答，了解更多提交信息。如果您遇到提交错误，请查看代码调试文档。

## 效率奖评估

### 效率奖

我们还举办了第二个专题研讨会，重点关注模型效率，因为高精度模型通常计算量很大。对于效率奖，我们将从运行时间和预测性能两个方面对参赛作品进行评估。要获得效率奖，必须提交以下材料：

- 必须是团队选出的排行榜奖参赛作品之一，或者是根据 “我的参赛作品” 选项卡中所述条件自动选出的参赛作品之一。

- 必须在私人排行榜上排名高于 `sample_submission.csv` 基准排名。

所有符合上述条件的参赛作品都将有机会角逐效率奖。一件参赛作品可能同时符合排行榜奖和效率奖的评选资格。符合条件的提交作品将根据其在私有测试数据上的以下评估指标排名获得效率奖。

### 效率得分

我们通过以下方式计算提交内容的效率得分：

$$
\text{Efficiency} = \frac{ \text{AUC} }{ \text{Benchmark} - \max\text{AUC} } + \frac{ \text{RuntimeSeconds} }{ 32400 }
$$

AUC 是参赛作品在主要指标上的得分；Benchmark 是基准测试的得分 `sample_submission.csv`，max AUC 是私人排行榜上的最大 AUC；RuntimeSeconds 是本次提交内容被评估所需的秒数。目标是尽可能降低效率得分。在比赛的训练阶段，您可以在以下笔记本中看到公开测试数据的排行榜，该排行榜每日更新。比赛结束后，我们将使用私有数据的效率得分更新此排行榜。在训练阶段，此排行榜仅显示各队的排名，不显示完整得分。

## 引用

```text
@misc{rsna-knee-abnormality-detection,
    author = {Po-Hao “Howard” Chen and Naveen Subhas and Robyn Ball and Pieter Baeyens and Errol Colak and Ali Emami and Hillary Garner and Jacob Kazam and Hui-Ming Lin and Luciano Prevedello and Daniel Schneider and Jason Sho and Ryan Holbrook and María Cruz},
    title = {RSNA Knee Abnormality Detection},
    year = {2026},
    howpublished = {\url{https://www.kaggle.com/competitions/rsna-knee-abnormality-detection}},
    note = {Kaggle}
}
```