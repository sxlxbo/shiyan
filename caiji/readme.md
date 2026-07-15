前置检查：

信号线：树莓派 GPIO 17 (物理11) -> 相机 J3-1

共地线：树莓派 GND（物理9） -> 相机 J3-4

WS2812B 软屏接线 (极其重要，千万别插错) 数据输入 (DIN)： 将软屏的 DIN (绿色信号线) 插到树莓派的 GPIO 10 / SPI0 MOSI（物理引脚 19）。
信号与电源地 (GND)： 将软屏的 GND (白线或细黑线) 插到树莓派的 物理引脚 20（紧挨着 19 号针脚的一个空闲 GND）。

5V 供电 (VCC)： 将软屏的 5V (红线) 插到树莓派的 物理引脚 2 或 4（这两个都是 5V Power）。

第一步 sudo dtoverlay genx320

第二步 ./rp5_setup_v4l.sh

第三步 metavision_viewer

录制 metavision_viewer -o wenjianming.raw

播放 metavision_viewer =i wenjianming.raw

## 同步采集（协议 v1）

三个脚本 `sync_protocol.py`、`receive_record.py`、`sync_led_trig.py` 必须放在同一目录。采集时长和周期只修改 `sync_protocol.py` 中的 `TOTAL_DURATION` 与 `RGB_INTERVAL_S`，发送端和接收端会读取同一份配置。

协议顺序为：3 个 PREAMBLE 脉冲、保护期、2 个 START 脉冲、正式 R/G/B 段、4 个 STOP 脉冲。Trigger 固定为 2 ms，并在 `pixels.show()` 返回后产生。

📺 终端 1（接收端）：
启动监听：
cd ~/genx320_test/

python3 receive_record.py

此时屏幕显示 `ARMED`。在识别到完整 PREAMBLE 前不会创建 RAW；默认等待 30 秒后超时退出。

📺 终端 2（控制端）：开启一个新的终端，启动光源和触发：
cd ~/genx320_test/

python3 sync_led_trig.py

正常完成后，`trigger_records/` 中会生成：

- `genx320_led_sync_时间.raw`：通过协议与数量校验的正式文件；
- 同名 `.csv`：Trigger 时间戳、通道、极性、RGB 序号和颜色；
- `led_schedule_时间.csv`：树莓派侧的计划/实际发送时刻和超期量。

录制中断、STOP 丢失或脉冲数量不符时，文件保留为 `.partial.raw` 供诊断，不会冒充成功结果。
