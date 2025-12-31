# 模块结构说明

本项目已成功将 `main.py` 拆分为多个功能模块，提高了代码的可维护性和可扩展性。

## 目录结构

```
src/
├── main.py                    # 程序入口
├── detection/                 # 目标检测模块
│   ├── __init__.py
│   └── yolo_detector.py      # YOLO目标检测器
├── onvif/                     # ONVIF控制模块
│   ├── __init__.py
│   └── onvif_controller.py   # ONVIF摄像机控制器
├── gui/                       # GUI界面模块
│   ├── __init__.py
│   └── player_window.py      # 主窗口界面类
├── rtsp/                      # RTSP流处理模块（已存在）
│   └── stream_handler.py
└── utils/                     # 工具模块（已存在）
    └── config.py
```

## 各模块说明

### 1. `src/main.py` - 程序入口
**作用**: 应用程序的主入口点，负责初始化主窗口和启动GUI事件循环。

**主要功能**:
- 创建Tkinter根窗口
- 实例化PlayerWindow
- 设置窗口关闭事件处理
- 启动主事件循环

**代码行数**: ~60行

---

### 2. `src/detection/yolo_detector.py` - YOLO目标检测器
**作用**: 提供基于YOLO模型的目标检测功能，支持实时视频流中的目标识别。

**主要功能**:
- YOLO模型加载和初始化
- 目标检测（支持自定义类别和置信度阈值）
- 检测结果绘制（在视频帧上绘制边界框和标签）
- 线程安全的检测操作

**主要类**:
- `YOLODetector`: YOLO检测器类
  - `__init__(model_path)`: 初始化检测器
  - `detect(frame, conf_threshold, target_classes, imgsz)`: 执行目标检测
  - `draw_detections(frame, detections, colors)`: 在帧上绘制检测结果

**代码行数**: ~162行

---

### 3. `src/onvif/onvif_controller.py` - ONVIF摄像机控制器
**作用**: 提供ONVIF协议支持，用于控制支持ONVIF的IP摄像机（PTZ控制）。

**主要功能**:
- ONVIF摄像机连接
- PTZ（平移、倾斜、变焦）控制
- 绝对移动、相对移动、持续移动
- 请求/响应日志记录

**主要类**:
- `ONVIFController`: ONVIF控制器类
  - `__init__(ip, port, username, password)`: 连接ONVIF摄像机
  - `get_profiles()`: 获取摄像机配置集
  - `absolute_move(pan, tilt, zoom, speed)`: 绝对移动
  - `relative_move(pan, tilt, zoom, speed)`: 相对移动
  - `continuous_move(pan, tilt, zoom, timeout)`: 持续移动
  - `relative_move_with_log(pan, tilt, zoom, speed)`: 相对移动并记录日志

**代码行数**: ~150行

---

### 4. `src/gui/player_window.py` - 主窗口界面
**作用**: 应用程序的主GUI界面，整合视频播放、PTZ控制、智能检测等功能。

**主要功能**:
- PotPlayer风格的深色主题界面
- RTSP视频流播放（支持硬件加速）
- 画中画（PIP）功能
- ONVIF PTZ控制面板
- 智能目标检测集成
- 检测结果显示
- FPS和分辨率信息显示
- 流管理和自动重启机制

**主要类**:
- `PlayerWindow(ttk.Frame)`: 主窗口类
  - `setup_theme()`: 设置界面主题
  - `create_widgets()`: 创建主界面组件
  - `create_ptz_controls()`: 创建PTZ控制面板
  - `play_pip()`: 播放视频流（支持画中画）
  - `stop_stream()`: 停止视频流
  - `_start_pip_stream()`: 视频流处理线程
  - `connect_onvif()`: 连接ONVIF摄像机
  - `move_camera(pan, tilt)`: 移动摄像机
  - `zoom_camera(zoom)`: 变焦控制
  - `toggle_ai_mode()`: 切换智能模式
  - `update_detection_display()`: 更新检测结果显示

**代码行数**: ~1240行

---

## 模块间依赖关系

```
main.py
  └── gui.player_window
        ├── detection.yolo_detector
        └── onvif.onvif_controller
```

## 优势

1. **模块化设计**: 每个模块职责单一，易于维护和测试
2. **可扩展性**: 新功能可以独立添加到相应模块
3. **代码复用**: 检测器和控制器可以在其他项目中复用
4. **清晰的结构**: 代码组织清晰，便于团队协作

## 使用说明

运行程序：
```bash
cd src
python main.py
```

或者从项目根目录：
```bash
python -m src.main
```

