# 单色时间流堆叠重建

把采集端产生的同名 `.raw` 和 `.json` 放在一起，然后运行：

```powershell
cd F:\Code\vscode\Python\shiyan\fenxi\mono_time_validation
python reconstruct_mono.py path\to\genx320_mono_red_*.raw
```

默认取每次 `MONO` Trigger 后 1–40 ms 的事件，剔除事件总数异常的周期，按像素融合
有效周期，做 3x3 中值去噪和稳健对比度拉伸。输出为 `output/*_stack.png` 与同名
`*.report.json`。这是单色事件响应/轮廓重建，不是普通相机的绝对灰度照片。

调参顺序：

1. 先看报告中的 `events_per_cycle`；若多数为 0，检查灯、接线和相机 bias。
2. 若边缘很弱，尝试 `--window-end-ms 60`。
3. 若背景噪声多，尝试缩短为 `--window-end-ms 20`。
4. 若细线被中值滤波擦除，加入 `--no-median`。

清晰度验收建议：目标主要轮廓可辨、各周期事件量处于同一量级、有效周期不少于总数
的 80%，且 `nonzero_pixel_ratio` 不应仅由少数固定热像素贡献。
