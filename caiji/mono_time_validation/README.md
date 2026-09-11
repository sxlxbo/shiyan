# 单色时间流采集验证

本程序让 WS2812B 在 `BLACK -> MONO -> BLACK` 间周期切换，同时用 GPIO 17
给 GenX320 发送 Trigger。相机记录的 Trigger 是分析端切分每次亮起响应的时间基准。

接线与原采集程序一致：GPIO 17（物理 11）接相机 J3-1，公共 GND 接 J3-4；
WS2812B DIN 接 GPIO 10/SPI0 MOSI（物理 19）。先执行原项目要求的 GenX320
overlay 和 `rp5_setup_v4l.sh`。

```bash
cd ~/genx320_test/caiji/mono_time_validation
python3 capture_mono.py --color red --brightness 0.20 --cycles 30
```

输出在 `records/`，每次实验包含同名 `.raw` 和 `.json`。建议物体与相机保持静止，
遮掉环境中的工频闪烁光，并让单色灯均匀覆盖目标。默认只点亮 16x16 灯板中心 4x4；
若照度不足，可先在确保电源和散热安全的前提下修改 `center_block_indices()`。

恒定光照下事件相机只响应亮度变化，所以程序采用同一单色光的开/关调制；它没有混入
其他颜色。不要用“灯一直亮着”的 RAW 判断重建算法失效。
