# GenX320 RGB 分析端（第二轮）

本目录实现 `rgb-black-sync-v2` 的经典分析基线。正式照明序列为：

```text
BLACK → R → BLACK → G → BLACK → B
```

程序将每个 `BLACK→R/G/B` 作为独立颜色响应，把 `Color→BLACK` 作为熄灭质量诊断，最终输出 R、G、B 单通道图、相对 RGB 四宫格、分色时序图和 JSON 报告。

当前结果表示统一黑场基准下的事件响应强度。没有灰卡或色卡标定时，它是未标定相对色彩/事件响应伪彩，不等同于普通相机的绝对 RGB。

## 输入

将同名 RAW 与 v2 sidecar 放入 `input/`：

```text
input/
├── genx320_led_sync_*.raw
└── genx320_led_sync_*.csv
```

CSV 当前为必需输入，并须包含：

```text
protocol_version,timestamp_us,id,polarity,role,state_index,cycle_index,phase_index,state
```

分析端会严格校验 `rgb-black-sync-v2`、六状态顺序、周期/相位编号、Trigger 通道与极性，并将每个 CSV 正式 Trigger 与 RAW 匹配。RAW 无法自行证明照明实际采用 v1 还是 v2，因此当前不会静默猜测协议。

## 运行

在已经配置 Metavision SDK 的环境中执行：

```powershell
cd F:\Code\vscode\Python\shiyan\fenxi
python reconstruct_rgb.py
```

多个 RAW 时需明确指定：

```powershell
python reconstruct_rgb.py input\sample.raw --metadata input\sample.csv
```

常用参数：

```text
--output FILE.png        指定输出位置
--calibration FILE.json  加载色彩标定
--save-features          保存 on/off 浮点中间特征
--no-titles              不绘制四宫格内部标签
```

v2 只提供 `response` 重建。旧版 `log-ls` 使用连续 `B→R/R→G/G→B` 方程，与插黑后的独立响应语义不兼容，已在 v2 路径中禁用。

默认输出：

```text
*_reconstruction.png          R/G/B/RGB 四宫格
*_reconstruction_timing.png   COLOR、R、G、B、BLACK 响应曲线
*_reconstruction.report.json  协议、窗口、样本、on/off、背景和警告
```

## 测试

测试使用合成 Trigger 和事件，不需要真实 RAW：

```powershell
python -m unittest discover -s tests -v
```

真实 v2 RAW/CSV 到位后，还需完成实样验收：Trigger 匹配、分色峰值、三通道空间对齐、有效样本率以及弱通道物理原因检查。
