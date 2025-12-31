import tkinter as tk
from tkinter import ttk, Label
from threading import Thread, Lock
import os
import queue
from PIL import Image, ImageTk, ImageDraw, ImageFont
import time
import subprocess
import numpy as np
# 可选使用 OpenCV 做快速缩放/颜色转换
try:
    import cv2
    CV2_AVAILABLE = True
except Exception:
    CV2_AVAILABLE = False
from tkinter import messagebox
from tkinter.scrolledtext import ScrolledText
from src.detection.yolo_detector import YOLODetector, YOLO_AVAILABLE
from src.onvif.onvif_controller import ONVIFController


class PlayerWindow(ttk.Frame):
    def __init__(self, parent):
        ttk.Frame.__init__(self, parent)
        self.parent = parent
        
        # 应用深色科技主题
        self.setup_theme()
        
        # 设置固定解码分辨率（不随窗口大小改变，避免频繁重启流）
        # 使用常见的视频分辨率，可以根据实际需求调整
        # 注意：降低解码分辨率可以提高帧率，但会降低画质
        # 建议：1080p适合大多数场景，720p可以获得更高帧率
        # 将解码分辨率提高到 2560x1440（2K），提高画面清晰度。
        # 注意：更高解码分辨率会显著增加CPU/GPU负载，可能影响帧率，
        # 建议同时启用硬件解码或升高 submit_interval / 降低检测尺寸以保持流畅。
        # self.decode_width = 2560  # 固定解码宽度（2K）
        # self.decode_height = 1440  # 固定解码高度（2K）
        self.decode_width = 3840  # 固定解码宽度（2K）
        self.decode_height = 2160  # 固定解码高度（2K）
        
        # 显示面板的尺寸会随窗口大小改变（用于缩放显示），但解码分辨率保持 2K，保证画质
        self.panel_width = 320
        self.panel_height = 180

        self.stream1_var = tk.StringVar(value="rtsp://172.20.4.99/live/VideoChannel1")
        self.stream2_var = tk.StringVar(value="rtsp://172.20.4.99/live/VideoChannel2")
        self.connection_status = tk.StringVar(value="未连接")
        self.stream_status = tk.StringVar(value="未播放")
        
        # 智能模式相关 - 必须在create_ptz_controls()之前初始化
        self.ai_mode_enabled = tk.BooleanVar(value=False)
        self.detect_person = tk.BooleanVar(value=True)
        self.detect_car = tk.BooleanVar(value=True)
        self.detect_drone = tk.BooleanVar(value=True)
        self.yolo_detector = None
        self.ai_lock = Lock()  # AI检测锁
        # 异步检测队列与线程（延迟初始化，启用智能模式时创建）
        self._detect_queue = None
        self._detect_thread = None
        self._last_detections = []
        
        # 画中画开关
        self.pip_enabled = tk.BooleanVar(value=True)  # 默认开启画中画
        # 是否使用 FFmpeg 在 C 层合并画中画（overlay），可以显著降低 Python 侧开销
        self.use_ffmpeg_pip = tk.BooleanVar(value=False)
        # 低延迟模式：优先降低延迟而非质量
        self.low_latency_mode = tk.BooleanVar(value=False)
        
        # 检测结果显示相关
        self.detection_results = []  # 存储当前检测结果
        self.detection_text_widget = None  # 检测结果显示文本框
        
        # 帧率统计相关
        self._fps_frame_times = []  # 存储最近帧的时间戳
        self._fps_window_size = 30  # 计算FPS的窗口大小（最近30帧）
        self._current_fps = 0.0  # 当前帧率
        self._last_fps_update = 0  # 上次FPS更新时间
        self._fps_update_interval = 0.5  # FPS更新间隔（秒）
        
        # 看门狗机制：检测线程是否卡死
        # 高质量拉流：增加看门狗超时时间，给网络波动更长的容忍度
        self._last_frame_time = 0  # 上次成功读取帧的时间
        # 将超时时间调大（默认10秒），避免因短暂网络抖动导致频繁重启
        self._frame_timeout = 10.0  # 如果超过此秒数没有新帧，认为卡死并重启流
        
        # 下采样检测配置（可调整以提高帧率）
        self.detect_downsample_size = 640  # 检测时的下采样尺寸，越小速度越快但精度可能降低
        # 可选值：640（平衡）、416（快速）、320（很快）、256（最快但精度较低）

        # 初始化硬件解码开关变量和日志目录（在创建控件之前）
        self.hw_accel_var = tk.BooleanVar(value=False)
        self.ffmpeg_log_dir = os.path.join(os.getcwd(), 'logs')
        try:
            os.makedirs(self.ffmpeg_log_dir, exist_ok=True)
        except Exception:
            pass

        # 先创建右侧控制面板，确保它先显示
        self.create_ptz_controls()
        # 再创建左侧视频区域
        self.create_widgets()
        
        self.stop_flag = False
        # 绑定窗口大小改变事件，以动态调整显示分辨率（但解码分辨率保持 2560x1440）
        self.panel1.bind("<Configure>", self.on_panel_resize)
        self.need_restart_stream = False
        self.onvif_controller = None
        self.send_text = None
        self.recv_text = None
        self.right_panel = None  # 保存右侧面板引用
        self.stream_thread = None  # 保存流线程引用
        self.ffmpeg_procs = []  # 保存FFmpeg进程列表，用于清理
        self.is_playing = False  # 防止重复播放
        # 重启退避控制
        self._restart_attempts = 0
        self._next_restart_time = 0
        self._max_backoff = 60  # 最大退避时间（秒）
        # CUDA使用与回退追踪
        self._last_hw_accel = None
        self._cuda_failures = 0
        self._cuda_disable_threshold = 3
        # 默认禁用硬件加速（强制使用软件解码），便于快速排查与稳定性验证
        # 如果需要开启硬件加速，请将此项设置为 False 或在运行时修改该属性
        self._cuda_disabled = False
        print("已强制使用软件解码（已禁用 CUDA 硬件加速）")
        # UI 更新队列：流线程将 PIL Image 放入队列，主线程消费并创建 PhotoImage
        import queue as _queue_module
        self._ui_queue = _queue_module.Queue(maxsize=1)

    def setup_theme(self):
        """设置 PotPlayer 风格主题"""
        style = ttk.Style()
        style.theme_use('clam')
        
        # PotPlayer 风格配色方案 - 深黑色主题
        bg_color = "#1a1a1a"  # 深黑背景（PotPlayer 风格）
        panel_bg = "#2a2a2a"  # 面板背景（稍亮）
        border_color = "#3a3a3a"  # 边框颜色
        text_color = "#e0e0e0"  # 主文字颜色（浅灰白）
        text_secondary = "#a0a0a0"  # 次要文字颜色
        accent_color = "#4a4a4a"  # 按钮背景
        hover_color = "#5a5a5a"  # 悬停颜色
        active_color = "#6a6a6a"  # 激活颜色
        entry_bg = "#2a2a2a"  # 输入框背景
        entry_fg = "#ffffff"  # 输入框文字
        highlight_color = "#0078d4"  # 高亮色（蓝色，PotPlayer 风格）
        
        # 配置样式
        style.configure('TFrame', background=bg_color, borderwidth=0)
        style.configure('TLabelFrame', background=panel_bg, foreground=text_color, 
                       borderwidth=1, relief='flat', bordercolor=border_color)
        style.configure('TLabelFrame.Label', background=panel_bg, foreground=text_color,
                       font=('Segoe UI', 9))
        style.configure('TLabel', background=bg_color, foreground=text_color,
                       font=('Segoe UI', 9))
        style.configure('TEntry', fieldbackground=entry_bg, foreground=entry_fg,
                       borderwidth=1, relief='flat', insertcolor=highlight_color,
                       bordercolor=border_color)
        style.configure('TButton', background=accent_color, foreground=text_color,
                       borderwidth=0, relief='flat', font=('Segoe UI', 9))
        style.map('TButton', 
                 background=[('active', hover_color), ('pressed', active_color)],
                 bordercolor=[('active', border_color)])
        
        # 自定义按钮样式 - 扁平化设计
        style.configure('Control.TButton', background=accent_color, foreground=text_color,
                       font=('Segoe UI', 11), width=4, borderwidth=0, relief='flat')
        style.map('Control.TButton', 
                 background=[('active', hover_color), ('pressed', active_color)])
        
        style.configure('Connect.TButton', background=highlight_color, foreground='white',
                       font=('Segoe UI', 9), borderwidth=0, relief='flat')
        style.map('Connect.TButton', background=[('active', '#0088e5'), ('pressed', '#0066b3')])
        
        style.configure('Play.TButton', background=highlight_color, foreground='white',
                       font=('Segoe UI', 10), borderwidth=0, relief='flat')
        style.map('Play.TButton', background=[('active', '#0088e5'), ('pressed', '#0066b3')])
        
        style.configure('Small.TButton', background=accent_color, foreground=text_color,
                       font=('Segoe UI', 8), borderwidth=0, relief='flat')
        style.map('Small.TButton', 
                 background=[('active', hover_color), ('pressed', active_color)])
        
        # 设置主窗口背景
        self.parent.configure(bg=bg_color)
        self.configure(style='TFrame')

    def create_widgets(self):
        """创建主界面组件 - PotPlayer 风格"""
        # 左侧主区域 - 视频流控制
        left_frame = ttk.Frame(self)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=0, pady=0)
        
        # 顶部工具栏 - PotPlayer 风格（紧凑、扁平）
        toolbar = tk.Frame(left_frame, bg="#1a1a1a", height=40)
        toolbar.pack(fill=tk.X, side=tk.TOP, padx=0, pady=0)
        toolbar.pack_propagate(False)
        
        # 左侧：流配置（紧凑布局）
        stream_config_frame = tk.Frame(toolbar, bg="#1a1a1a")
        stream_config_frame.pack(side=tk.LEFT, padx=8, pady=5)
        
        tk.Label(stream_config_frame, text="主:", bg="#1a1a1a", fg="#a0a0a0", 
                font=('Segoe UI', 8)).pack(side=tk.LEFT, padx=(0, 3))
        stream1_entry = ttk.Entry(stream_config_frame, textvariable=self.stream1_var, width=35)
        stream1_entry.pack(side=tk.LEFT, padx=(0, 8))
        
        tk.Label(stream_config_frame, text="画中画:", bg="#1a1a1a", fg="#a0a0a0", 
                font=('Segoe UI', 8)).pack(side=tk.LEFT, padx=(0, 3))
        stream2_entry = ttk.Entry(stream_config_frame, textvariable=self.stream2_var, width=25)
        stream2_entry.pack(side=tk.LEFT, padx=(0, 5))
        
        # 画中画开关复选框
        pip_check = tk.Checkbutton(stream_config_frame, text="启用", variable=self.pip_enabled,
                                   bg="#1a1a1a", fg="#a0a0a0", selectcolor="#2a2a2a",
                                   activebackground="#1a1a1a", activeforeground="#00d4aa",
                                   font=('Segoe UI', 8))
        pip_check.pack(side=tk.LEFT)

        # FFmpeg PIP 合并开关（将两路流交由 FFmpeg overlay 合并，减少 Python 端像素写入）
        ffmpeg_pip_check = tk.Checkbutton(stream_config_frame, text="FFmpeg 合并 PIP", variable=self.use_ffmpeg_pip,
                                          bg="#1a1a1a", fg="#a0a0a0", selectcolor="#2a2a2a",
                                          activebackground="#1a1a1a", activeforeground="#00d4aa",
                                          font=('Segoe UI', 8))
        ffmpeg_pip_check.pack(side=tk.LEFT, padx=(6, 0))

        # 低延迟模式开关
        latency_check = tk.Checkbutton(stream_config_frame, text="低延迟", variable=self.low_latency_mode,
                                       bg="#1a1a1a", fg="#a0a0a0", selectcolor="#2a2a2a",
                                       activebackground="#1a1a1a", activeforeground="#00d4aa",
                                       font=('Segoe UI', 8))
        latency_check.pack(side=tk.LEFT, padx=(6, 0))        # 中间：播放控制按钮
        control_frame = tk.Frame(toolbar, bg="#1a1a1a")
        control_frame.pack(side=tk.LEFT, padx=15, pady=5)
        
        play_button = ttk.Button(control_frame, text="▶", 
                                command=self.play_pip, style='Play.TButton', width=3)
        play_button.pack(side=tk.LEFT, padx=2)
        stop_button = ttk.Button(control_frame, text="⏸", 
                                command=self.stop_stream, style='Small.TButton', width=3)
        stop_button.pack(side=tk.LEFT, padx=2)
        
        # 右侧：智能模式开关
        ai_frame = tk.Frame(toolbar, bg="#1a1a1a")
        ai_frame.pack(side=tk.RIGHT, padx=10, pady=5)
        
        ai_check = tk.Checkbutton(ai_frame, text="智能模式", variable=self.ai_mode_enabled,
                                  bg="#1a1a1a", fg="#a0a0a0", selectcolor="#2a2a2a",
                                  activebackground="#1a1a1a", activeforeground="#00d4aa",
                                  font=('Segoe UI', 8), command=self.toggle_ai_mode)
        ai_check.pack(side=tk.LEFT, padx=(0, 10))
        # 将硬件解码开关移到工具栏，靠近智能模式
        hw_toolbar_check = tk.Checkbutton(ai_frame, text="硬件解码", variable=self.hw_accel_var,
                          bg="#1a1a1a", fg="#a0a0a0", selectcolor="#2a2a2a",
                          activebackground="#1a1a1a", activeforeground="#00d4aa",
                          font=('Segoe UI', 8), command=self._toggle_hw_accel)
        hw_toolbar_check.pack(side=tk.LEFT)
        
        # 状态显示
        status_frame = tk.Frame(toolbar, bg="#1a1a1a")
        status_frame.pack(side=tk.RIGHT, padx=10, pady=5)
        
        tk.Label(status_frame, text="状态:", bg="#1a1a1a", fg="#a0a0a0", 
                font=('Segoe UI', 8)).pack(side=tk.LEFT, padx=(0, 5))
        self.status_label = tk.Label(status_frame, textvariable=self.stream_status, 
                                    bg="#1a1a1a", fg="#00d4aa", font=('Segoe UI', 8, 'bold'))
        self.status_label.pack(side=tk.LEFT)
        
        # 视频显示面板 - PotPlayer 风格（无边框，纯黑背景）
        video_container = tk.Frame(left_frame, bg="#000000")
        video_container.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)
        
        self.panel1 = Label(video_container, bg="#000000", relief='flat', borderwidth=0)
        self.panel1.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)
        
        # 占位文本 - PotPlayer 风格
        placeholder = Label(video_container, text="等待视频流...", 
                           bg="#000000", fg="#666666", font=('Segoe UI', 12))
        placeholder.place(relx=0.5, rely=0.5, anchor='center')
        self.panel1.placeholder = placeholder
        
        # 浮动信息标签：FPS 与分辨率（使用独立控件替代每帧绘制）
        self.fps_label = tk.Label(video_container, text="FPS: 0.0", bg="#000000", fg="#00d4aa",
                      font=('Segoe UI', 10, 'bold'))
        self.fps_label.place(x=10, y=8)
        self.res_label = tk.Label(video_container, text="", bg="#000000", fg="#ffffff",
                      font=('Segoe UI', 8))
        self.res_label.place(x=10, y=30)

    def stop_stream(self):
        """停止视频流"""
        self.stop_flag = True
        self.is_playing = False
        self.stream_status.set("已停止")
        self.status_label.config(fg="#a0a0a0")
        # 清理所有FFmpeg进程
        self._cleanup_ffmpeg_procs()
        # 恢复占位文本
        if not hasattr(self.panel1, 'placeholder') or not self.panel1.placeholder:
            placeholder = Label(self.panel1.master, text="等待视频流...", 
                             bg="#000000", fg="#666666", font=('Segoe UI', 12))
            placeholder.place(relx=0.5, rely=0.5, anchor='center')
            self.panel1.placeholder = placeholder
    
    def _cleanup_ffmpeg_procs(self):
        """清理所有FFmpeg进程"""
        for proc in self.ffmpeg_procs:
            if proc and proc.poll() is None:  # 进程仍在运行
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                        proc.wait(timeout=1)
                    except Exception:
                        pass
                except Exception as e:
                    print(f"清理FFmpeg进程错误: {e}")
                    try:
                        proc.kill()
                    except Exception:
                        pass
        self.ffmpeg_procs.clear()

    def on_panel_resize(self, event):
        try:
            # 更新显示面板的尺寸以适应窗口大小改变（保持 16:9 宽高比）
            # 但解码分辨率保持固定为 2560x1440，确保画面清晰度
            self.panel_width = event.width
            self.panel_height = int(self.panel_width * 9 / 16)
            self.panel1.config(width=self.panel_width, height=self.panel_height)
            # 解码分辨率已固定，无需重启流
        except Exception as e:
            print("处理窗口大小调整异常:", e)

    def play_pip(self):
        # 防止重复播放
        if self.is_playing:
            return
        
        # 如果已有线程在运行，先停止
        if self.stream_thread and self.stream_thread.is_alive():
            self.stop_stream()
            # 等待线程结束
            self.stream_thread.join(timeout=1)
        
        self.stop_flag = False
        self.is_playing = True
        self.stream_status.set("连接中...")
        self.status_label.config(fg="#ffaa00")
        # 隐藏占位文本
        if hasattr(self.panel1, 'placeholder') and self.panel1.placeholder.winfo_exists():
            self.panel1.placeholder.destroy()
            self.panel1.placeholder = None
        self.stream_thread = Thread(target=self._start_pip_stream, daemon=True)
        self.stream_thread.start()

    def _start_pip_stream(self):
        def ffmpeg_stream(url, width, height, use_hw=True):
            """创建FFmpeg进程，支持多种硬件加速（CUDA、QSV、VAAPI）和软件解码降级"""
            print(f"启动FFmpeg流: {url}，分辨率: {width}x{height}，硬件解码: {use_hw}")
            # 根据低延迟模式调整参数
            is_low_latency = self.low_latency_mode.get()
            base_cmd = [
                'ffmpeg',
                '-loglevel', 'warning',
                '-hide_banner',
                '-nostdin',
                '-rtsp_transport', 'tcp',  # 使用TCP传输，更稳定
                '-use_wallclock_as_timestamps', '1',
                # 根据模式选择配置：低延迟优先还是质量优先
                '-fflags', '+genpts+discardcorrupt',  # 生成PTS，丢弃损坏帧
                '-flags', '+low_delay',    # 低延迟但不完全禁用缓冲
                '-strict', 'experimental',
                '-protocol_whitelist', 'rtsp,udp,rtp,file,http,https,tcp',
                '-i', url,
                '-f', 'rawvideo',
                '-pix_fmt', 'rgb24',
                '-s', f'{width}x{height}',
                # 移除帧率限制，让FFmpeg自动适应源流帧率，保证质量
                # '-r', '15',  # 已移除，避免强制降帧导致卡顿
                '-vsync', '0',  # 禁用帧同步，直接传递所有帧
            ]
            
            # 根据低延迟模式调整缓冲与分析参数
            if is_low_latency:
                # 低延迟模式：减小缓冲、快速探测、降低分析时间，并尝试禁用内部缓冲
                base_cmd.extend([
                    '-fflags', 'nobuffer',
                    '-rtsp_flags', 'nobuffer',
                    '-flush_packets', '1',
                    '-max_delay', '50000',     # 最大延迟 50ms
                    '-reorder_queue_size', '0', # 禁用重排序队列
                    '-analyzeduration', '500000',  # 分析时长 0.5s
                    '-probesize', '32768',      # 探测大小 32KB（快速但可能降低兼容性）
                ])
            else:
                # 质量优先模式：大缓冲和长延时，优先保证质量
                base_cmd.extend([
                    '-max_delay', '2000000',    # 最大延迟 2s
                    '-reorder_queue_size', '0', # 禁用重排序队列（实时流）
                    '-analyzeduration', '10000000',  # 分析时长 10s
                    '-probesize', '10000000',   # 探测大小 10MB
                ])
            
            base_cmd.append('-')
            # 尝试多种硬件加速方式（按优先级顺序），如果被标记为禁用则跳过
            if use_hw and not getattr(self, '_cuda_disabled', False):
                hw_accels = [
                    # NVIDIA CUDA
                    {
                        'name': 'CUDA',
                        'cmd': ['ffmpeg', '-hwaccel', 'cuda', '-hwaccel_device', '0',
                               '-c:v', 'h264_cuvid'] + base_cmd[1:],
                    },
                    # Intel Quick Sync Video
                    {
                        'name': 'QSV',
                        'cmd': ['ffmpeg', '-hwaccel', 'qsv', '-c:v', 'h264_qsv'] + base_cmd[1:],
                    },
                    # VAAPI (Linux)
                    {
                        'name': 'VAAPI',
                        'cmd': ['ffmpeg', '-hwaccel', 'vaapi', '-hwaccel_device', '/dev/dri/renderD128',
                               '-c:v', 'h264_vaapi'] + base_cmd[1:],
                    },
                ]

                for hw_accel in hw_accels:
                    try:
                        proc = subprocess.Popen(hw_accel['cmd'], stdout=subprocess.PIPE,
                                                stderr=subprocess.PIPE, bufsize=1024*1024 if self.low_latency_mode.get() else 1024*1024)
                        # 检查进程是否正常启动
                        #time.sleep(0.1)
                        if proc.poll() is None:  # 进程仍在运行
                            print(f"使用 {hw_accel['name']} 硬件加速解码")
                            # 记录上一次使用的硬件加速类型，供回退逻辑判断
                            try:
                                self._last_hw_accel = hw_accel['name']
                            except Exception:
                                pass
                            # 启动后台线程持续读取 stderr，避免管道填满导致 FFmpeg 阻塞
                            def _drain_stderr(p, tag):
                                def _run():
                                    try:
                                        while True:
                                            line = p.stderr.readline()
                                            if not line:
                                                if p.poll() is not None:
                                                    break
                                                #time.sleep(0.01)
                                                continue
                                            try:
                                                s = line.decode('utf-8', errors='ignore').strip()
                                                if s:
                                                    print(f"[ffmpeg {tag}] {s[:500]}")
                                            except Exception:
                                                pass
                                    except Exception:
                                        pass
                                Thread(target=_run, daemon=True).start()
                            try:
                                _drain_stderr(proc, hw_accel['name'])
                            except Exception:
                                pass
                            return proc
                        else:
                            # 读取错误信息
                            try:
                                _, stderr = proc.communicate(timeout=0.1)
                                if stderr:
                                    print(f"{hw_accel['name']} 硬件加速失败: {stderr.decode('utf-8', errors='ignore')[:400]}")
                            except:
                                pass
                    except Exception as e:
                        print(f"{hw_accel['name']} 硬件加速不可用: {e}")
                        continue

            # 降级到软件解码（使用libx264解码器）
            try:
                self._last_hw_accel = None
            except Exception:
                pass
            print("使用软件解码（libx264）")
            # 根据低延迟模式选择缓冲大小
            buffer_size = 1024*1024 if self.low_latency_mode.get() else 10*1024*1024
            proc = subprocess.Popen(base_cmd, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, bufsize=buffer_size)
            # 启动 stderr draining 线程
            try:
                def _drain_stderr_sw(p, tag='sw'):
                    def _run():
                        try:
                            while True:
                                line = p.stderr.readline()
                                if not line:
                                    if p.poll() is not None:
                                        break
                                    #time.sleep(0.01)
                                    continue
                                try:
                                    s = line.decode('utf-8', errors='ignore').strip()
                                    if s:
                                        print(f"[ffmpeg {tag}] {s[:500]}")
                                except Exception:
                                    pass
                        except Exception:
                            pass
                    Thread(target=_run, daemon=True).start()
                _drain_stderr_sw(proc, 'sw')
            except Exception:
                pass
            return proc

        def ffmpeg_stream_overlay(main_url, pip_url, width, height, pip_w, pip_h, use_hw=True):
            """使用 FFmpeg 在 C 层将两路流 overlay 合并后输出 rawvideo 到 stdout
            main_url: 主流 rtsp
            pip_url: 画中画 rtsp
            width,height: 输出主画面尺寸
            pip_w,pip_h: 画中画尺寸
            返回单一 proc（stdout 为合并后 rawvideo）
            """
            print(f"启动 FFmpeg overlay 合并: main={main_url}, pip={pip_url}, out={width}x{height}, pip={pip_w}x{pip_h}, hw={use_hw}")
            # 计算 overlay 位置（右下角，10px 内边距）
            overlay_x = max(0, width - pip_w - 10)
            overlay_y = max(0, height - pip_h - 10)

            # filter: scale second input then overlay
            filter_complex = f"[1:v]scale={pip_w}:{pip_h}[pip];[0:v][pip]overlay={overlay_x}:{overlay_y}"

            cmd = [
                'ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'warning',
                '-rtsp_transport', 'tcp',
                '-i', main_url,
                '-rtsp_transport', 'tcp',
                '-i', pip_url,
                '-filter_complex', filter_complex,
                '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{width}x{height}',
                '-vsync', '0', '-an', '-'
            ]

            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1024*1024 if self.low_latency_mode.get() else 10*1024*1024)
                # drain stderr to avoid blocking
                def _drain(p):
                    try:
                        while True:
                            line = p.stderr.readline()
                            if not line:
                                if p.poll() is not None:
                                    break
                                continue
                            try:
                                s = line.decode('utf-8', errors='ignore').strip()
                                if s:
                                    print(f"[ffmpeg overlay] {s[:400]}")
                            except Exception:
                                pass
                    except Exception:
                        pass
                Thread(target=_drain, args=(proc,), daemon=True).start()
                return proc
            except Exception as e:
                print(f"启动 overlay FFmpeg 失败: {e}")
                return None
        
        def read_with_timeout_threaded(pipe, size, timeout=10.0):
            """使用线程实现真正的非阻塞读取，防止卡死
            高质量拉流：增加超时时间到10秒，给网络波动更长的容忍度
            像专业播放器一样，优先保证质量而不是速度
            """
            import queue
            result_queue = queue.Queue()
            error_queue = queue.Queue()
            
            def read_thread():
                """在单独线程中执行阻塞读取"""
                try:
                    data = pipe.read(size)
                    result_queue.put(data)
                except Exception as e:
                    error_queue.put(e)
            
            # 启动读取线程
            read_thread_obj = Thread(target=read_thread, daemon=True)
            read_thread_obj.start()
            
            # 等待结果，带超时（增加到3秒，给网络波动更多容忍度）
            try:
                # 使用queue.get的超时功能
                result = result_queue.get(timeout=timeout)
                return result
            except queue.Empty:
                # 超时，返回None
                # 注意：读取线程可能仍在运行，但会被daemon标记自动清理
                return None
            except Exception as e:
                # 读取错误
                return None

        try:
            # 使用固定的解码分辨率，不随窗口大小改变
            decode_w, decode_h = self.decode_width, self.decode_height
            # 显示尺寸（用于画中画和最终显示）
            display_w, display_h = self.panel_width, self.panel_height
            pip_w, pip_h = display_w // 3, display_h // 3
            proc1 = None
            proc2 = None
            
            try:
                # 使用固定解码分辨率
                proc1 = ffmpeg_stream(self.stream1_var.get(), decode_w, decode_h, use_hw=bool(self.hw_accel_var.get()))
                # 根据画中画开关决定是否启动Stream 2（画中画也使用固定分辨率）
                if self.pip_enabled.get():
                    proc2 = ffmpeg_stream(self.stream2_var.get(), decode_w // 3, decode_h // 3, use_hw=bool(self.hw_accel_var.get()))
                    self.ffmpeg_procs = [proc1, proc2]
                else:
                    proc2 = None
                    self.ffmpeg_procs = [proc1]
                # 初始化帧计数和FPS统计
                self._frame_count = 0
                self._fps_frame_times = []
                self._current_fps = 0.0
                self._last_fps_update = time.time()
                self._last_frame_time = time.time()  # 初始化看门狗时间
            except Exception as e:
                def update_status():
                    self.stream_status.set("连接失败")
                    self.status_label.config(fg="#ff6666")
                    self.is_playing = False
                    # 恢复占位文本
                    if not hasattr(self.panel1, 'placeholder') or not self.panel1.placeholder:
                        placeholder = Label(self.panel1.master, text="等待视频流...", 
                                         bg="#000000", fg="#666666", font=('Segoe UI', 12))
                        placeholder.place(relx=0.5, rely=0.5, anchor='center')
                        self.panel1.placeholder = placeholder
                self.panel1.after(0, update_status)
                print(f"启动流失败: {e}")
                return
            
            # 使用解码分辨率的帧大小
            frame_size1 = decode_w * decode_h * 3
            frame_size2 = (decode_w // 3) * (decode_h // 3) * 3 if self.pip_enabled.get() else 0

            error_count = 0  # 新增异常计数
            # 高质量拉流：增加连续错误阈值，避免因短暂网络波动频繁重启
            max_error_count = 30  # 连续异常阈值（从10增加到30，质量优先）

            while not self.stop_flag:
                if self.need_restart_stream:
                    # 优化重启流程：先启动新流，再关闭旧流，实现无缝切换
                    # 如果尚未到允许的下次重启时间，则跳过本次重启尝试
                    if time.time() < getattr(self, '_next_restart_time', 0):
                        # 等待退避期结束
                        time.sleep(0.1)
                        continue
                    # 保存最后一帧，在重启期间继续显示，避免黑屏
                    last_frame_img = None
                    try:
                        if hasattr(self, 'panel1') and hasattr(self.panel1, 'imgtk') and self.panel1.imgtk:
                            last_frame_img = self.panel1.imgtk
                    except:
                        pass
                    
                    # 先启动新流（在后台）
                    decode_w, decode_h = self.decode_width, self.decode_height
                    new_proc1 = None
                    new_proc2 = None
                    try:
                        new_proc1 = ffmpeg_stream(self.stream1_var.get(), decode_w, decode_h, use_hw=bool(self.hw_accel_var.get()))
                        # 快速检查新流是否启动成功
                        time.sleep(0.001)
                        if new_proc1.poll() is not None:
                            raise Exception("新流启动失败")
                        
                        if self.pip_enabled.get():
                            new_proc2 = ffmpeg_stream(self.stream2_var.get(), decode_w // 3, decode_h // 3, use_hw=bool(self.hw_accel_var.get()))
                            time.sleep(0.05)
                            if new_proc2.poll() is not None:
                                new_proc2 = None
                    except Exception as e:
                        print(f"启动新流失败: {e}")
                        if new_proc1:
                            try:
                                new_proc1.kill()
                            except:
                                pass
                        if new_proc2:
                            try:
                                new_proc2.kill()
                            except:
                                pass
                        # 如果新流启动失败，继续使用旧流
                        self.need_restart_stream = False
                        continue
                    
                    # 新流启动成功后，快速关闭旧流
                    if proc1:
                        try:
                            proc1.terminate()
                            proc1.wait(timeout=0.001)  # 减少等待时间
                        except:
                            try:
                                proc1.kill()
                            except:
                                pass
                    if proc2:
                        try:
                            proc2.terminate()
                            proc2.wait(timeout=0.001)
                        except:
                            try:
                                proc2.kill()
                            except:
                                pass
                    
                    # 切换到新流
                    proc1 = new_proc1
                    proc2 = new_proc2
                    self.ffmpeg_procs = [proc1] if not proc2 else [proc1, proc2]
                    
                    # 如果新流还没准备好，继续显示最后一帧（避免黑屏）
                    if last_frame_img:
                        try:
                            self.panel1.after_idle(self._update_panel, last_frame_img)
                        except:
                            pass
                    
                    # 重置帧计数和FPS统计
                    if hasattr(self, '_frame_count'):
                        self._frame_count = 0
                    self._fps_frame_times = []
                    self._current_fps = 0.0
                    self._last_fps_update = time.time()
                    self._last_frame_time = time.time()  # 重置看门狗时间
                    # 重启成功，清除退避计数
                    self._restart_attempts = 0
                    self._next_restart_time = 0
                    
                    frame_size1 = decode_w * decode_h * 3
                    frame_size2 = (decode_w // 3) * (decode_h // 3) * 3 if self.pip_enabled.get() else 0
                    self.need_restart_stream = False
                    error_count = 0
                    # 减少等待时间，快速恢复（从0.2秒减少到0.05秒）
                    time.sleep(0.001)

                start_time = time.time()
                
                # 看门狗检查：如果超过5秒没有新帧，认为卡死，重启流
                if self._last_frame_time > 0 and (time.time() - self._last_frame_time) > self._frame_timeout:
                    print(f"检测到流卡死（超过{self._frame_timeout}秒无新帧），计划重启...")
                    self._schedule_restart(reason=f"watchdog timeout {self._frame_timeout}s")
                    continue
                
                try:
                    # 检查进程是否还在运行
                    if proc1.poll() is not None:
                        print("FFmpeg进程已退出，计划重启流...")
                        self._schedule_restart(reason="ffmpeg exited")
                        continue
                    
                    # 诊断：记录读取前的时间
                    _read_start = time.time()
                    
                    # 使用线程+队列实现非阻塞读取，避免阻塞卡死
                    # 高质量拉流：超时时间增加到10秒，给网络波动更长的容忍度
                    # 优先保证视频质量，允许更长的等待时间
                    # 根据低延迟模式调整读取超时，防止长超时掩盖积压
                    read_timeout = 2.0 if self.low_latency_mode.get() else 10.0
                    raw_frame1 = read_with_timeout_threaded(proc1.stdout, frame_size1, timeout=read_timeout)
                    _read_end = time.time()
                    _read_time = (_read_end - _read_start) * 1000
                    
                    # 如果读取超时或数据不完整，直接丢弃
                    if raw_frame1 is None or len(raw_frame1) != frame_size1:
                        error_count += 1
                        if error_count > max_error_count:
                            print(f"连续{max_error_count}次读取失败，计划重启流...")
                            self._schedule_restart(reason=f"continuous read failures {error_count}")
                        # 添加短暂延迟，避免CPU占用过高
                        time.sleep(0.001)
                        continue
                    
                    # 成功读取帧，更新看门狗时间和帧计数
                    self._last_frame_time = time.time()
                    error_count = 0  # 重置错误计数
                    
                    # 更新帧计数（在成功读取后）
                    if not hasattr(self, '_frame_count'):
                        self._frame_count = 0
                    self._frame_count += 1
                    
                    # 诊断：统计帧间隔
                    if not hasattr(self, '_last_frame_read_time'):
                        self._last_frame_read_time = _read_start
                    frame_interval = (_read_start - self._last_frame_read_time) * 1000
                    self._last_frame_read_time = _read_start
                    
                    # 诊断：每100帧打印一次详细的性能数据（避免日志太多）
                    if self._frame_count % 100 == 0:
                        print(f"[诊断] 帧 {self._frame_count}: 读取耗时={_read_time:.1f}ms, 帧间隔={frame_interval:.1f}ms")
                    
                    # 使用解码分辨率重塑帧（默认尽量避免拷贝）
                    _time1 = time.time()
                    frame1 = np.frombuffer(raw_frame1, np.uint8).reshape((decode_h, decode_w, 3))
                    # 如果启用了画中画并且需要在主帧上写入画中画内容，
                    # 必须确保 frame1 可写（从 bytes 创建的 array 可能是只读），
                    # 所以在这种情况下做一次显式拷贝。
                    if self.pip_enabled.get() and proc2 and frame_size2 > 0:
                        try:
                            # 仅在确实要修改主帧时拷贝以减少开销
                            frame1 = frame1.copy()
                        except Exception:
                            # 如果拷贝失败，则继续以防止程序崩溃，后续写入会抛出异常并被捕获
                            pass
                    
                    # 根据画中画开关决定是否叠加Stream 2
                    # 如果启用了 FFmpeg overlay 模式（use_ffmpeg_pip），则合并在 FFmpeg 层已经完成，
                    # Python 不再需要读取第二路并写入主帧。
                    if self.pip_enabled.get() and (not self.use_ffmpeg_pip.get()) and proc2 and frame_size2 > 0:
                        try:
                            # 画中画也使用线程+队列非阻塞读取，但超时时间更短（1秒）
                            # 画中画也使用线程+队列非阻塞读取，但超时时间稍短（8秒）
                            # 高质量拉流：给画中画也足够的缓冲时间
                            raw_frame2 = read_with_timeout_threaded(proc2.stdout, frame_size2, timeout= min(2.0, read_timeout) )
                            if raw_frame2 is not None and len(raw_frame2) == frame_size2:
                                pip_decode_w, pip_decode_h = decode_w // 3, decode_h // 3
                                frame2 = np.frombuffer(raw_frame2, np.uint8).reshape((pip_decode_h, pip_decode_w, 3))
                                # 计算画中画在解码分辨率中的位置
                                x_offset = max(0, decode_w - pip_decode_w - 10)
                                y_offset = max(0, decode_h - pip_decode_h - 10)
                                # 边界检查，防止数组越界
                                if (x_offset + pip_decode_w <= decode_w and y_offset + pip_decode_h <= decode_h and 
                                    x_offset >= 0 and y_offset >= 0):
                                    frame1[y_offset:y_offset+pip_decode_h, x_offset:x_offset+pip_decode_w] = frame2
                        except (ValueError, IndexError) as e:
                            print(f"读取画中画流失败: {e}")
                            # 画中画读取失败不影响主画面显示
                        except Exception as e:
                            print(f"画中画处理异常: {type(e).__name__}: {e}")
                    
                    # 转换为PIL Image（优先用 OpenCV 做缩放，这通常比 PIL 快）
                    # frame1 是 numpy 数组 (H, W, 3)，dtype=uint8，颜色顺序应为 RGB
                    frame_np = frame1  # 保持原名称，后面可能用到
                    _time2 = time.time()
                    print(f"帧处理时间: {(_time2 - _time1)*1000:.1f} ms")
                    
                    # 获取当前显示尺寸（缓存显示尺寸，避免频繁调用winfo，提高性能）
                    # 只在窗口大小改变时更新（通过on_panel_resize）
                    display_w, display_h = self.panel_width, self.panel_height
                    
                    # 确保显示尺寸有效
                    if display_w <= 0 or display_h <= 0:
                        display_w, display_h = 640, 360  # 默认尺寸
                    
                    # 如果解码分辨率和显示尺寸不同，进行缩放以适应显示面板
                    # 保持宽高比，避免图像变形
                    _time3 = time.time()
                    if decode_w != display_w or decode_h != display_h:
                        # 计算缩放比例，保持宽高比
                        scale_w = display_w / decode_w
                        scale_h = display_h / decode_h
                        scale = min(scale_w, scale_h)  # 使用较小的比例，确保图像完全显示

                        # 计算缩放后的尺寸
                        new_w = int(decode_w * scale)
                        new_h = int(decode_h * scale)

                        if CV2_AVAILABLE:
                            try:
                                # OpenCV 使用 (width, height) 参数顺序
                                resized_np = cv2.resize(frame_np, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
                                # resized_np 仍为 RGB 顺序，因为我们没有交换通道，仅做缩放
                                img = Image.fromarray(resized_np)
                            except Exception:
                                # 回退到 PIL 缩放（极少发生）
                                img = Image.fromarray(frame_np)
                                img = img.resize((new_w, new_h), Image.Resampling.BILINEAR)
                        else:
                            # 如果没有 OpenCV，则使用 PIL 缩放（原实现）
                            img = Image.fromarray(frame_np)
                            img = img.resize((new_w, new_h), Image.Resampling.BILINEAR)
                    else:
                        # 如果无需缩放（显示尺寸与解码一致），直接创建 PIL 对象
                        img = Image.fromarray(frame_np)

                    _time4 = time.time()
                    print(f"图像缩放时间: {(_time4 - _time3)*1000:.1f} ms")

                    # 计算帧率
                    current_frame_time = time.time()
                    self._fps_frame_times.append(current_frame_time)
                    # 只保留最近N帧的时间戳
                    if len(self._fps_frame_times) > self._fps_window_size:
                        self._fps_frame_times.pop(0)
                    
                    # 更新FPS（降低更新频率）
                    if current_frame_time - self._last_fps_update >= self._fps_update_interval:
                        if len(self._fps_frame_times) >= 2:
                            time_span = self._fps_frame_times[-1] - self._fps_frame_times[0]
                            if time_span > 0:
                                self._current_fps = (len(self._fps_frame_times) - 1) / time_span
                            else:
                                self._current_fps = 0.0
                        self._last_fps_update = current_frame_time
                    
                    # 使用浮动Label显示FPS与分辨率，避免每帧在图像上绘制文本的开销
                    try:
                        fps_text = f"FPS: {self._current_fps:.1f}"
                        res_text = f"{decode_w}x{decode_h} -> {self.panel_width}x{self.panel_height}"
                        # 在主线程更新Label
                        try:
                            self.panel1.after(0, lambda: (self.fps_label.config(text=fps_text), self.res_label.config(text=res_text)))
                        except Exception:
                            pass
                    except Exception:
                        pass
                    
                    # 智能模式：目标检测（改为异步检测队列，主线程不阻塞）
                    if self.ai_mode_enabled.get() and self.yolo_detector and self.yolo_detector.is_loaded:
                        try:
                            # 获取要检测的类别
                            target_classes = []
                            if self.detect_person.get():
                                target_classes.append('person')
                            if self.detect_car.get():
                                target_classes.extend(['car', 'truck', 'bus', 'motorcycle', 'bicycle'])
                            if self.detect_drone.get():
                                target_classes.append('drone')

                            # 每 N 帧向检测队列提交一帧（非阻塞）
                            submit_interval = 10  # 可以调整（越大检测频率越低但CPU占用更小）
                            if (self._frame_count % submit_interval) == 0:
                                try:
                                    target_detect_size = self.detect_downsample_size
                                    detect_scale = target_detect_size / max(decode_w, decode_h)
                                    detect_w = int(decode_w * detect_scale)
                                    detect_h = int(decode_h * detect_scale)
                                    detect_w = detect_w if detect_w % 2 == 0 else detect_w + 1
                                    detect_h = detect_h if detect_h % 2 == 0 else detect_h + 1

                                    # 下采样成较小尺寸以降低推理成本
                                    if detect_scale < 0.5:
                                        step = max(1, int(1.0 / detect_scale))
                                        detect_frame_np = frame1[::step, ::step, :]
                                        if detect_frame_np.shape[0] != detect_h or detect_frame_np.shape[1] != detect_w:
                                            detect_frame_np = np.array(Image.fromarray(detect_frame_np).resize((detect_w, detect_h), Image.Resampling.NEAREST))
                                    else:
                                        detect_frame = Image.fromarray(frame1)
                                        detect_frame = detect_frame.resize((detect_w, detect_h), Image.Resampling.NEAREST)
                                        detect_frame_np = np.array(detect_frame)

                                    # scale_back 用于将检测框从下采样坐标映射回解码分辨率
                                    scale_back = 1.0 / detect_scale if detect_scale > 0 else 1.0

                                    # 非阻塞放入队列（若队列已满则丢弃最新帧）
                                    try:
                                        if self._detect_queue is not None:
                                            self._detect_queue.put_nowait((detect_frame_np, target_classes if target_classes else None, float(self.conf_threshold.get()), int(target_detect_size), float(scale_back), decode_w, decode_h))
                                    except Exception:
                                        # 队列满或其他错误，忽略以保持主线程不阻塞
                                        pass
                                except Exception:
                                    pass

                            # 使用最近一次异步检测结果进行绘制
                            detections = []
                            try:
                                detections = getattr(self, '_last_detections', []) or []
                            except Exception:
                                detections = []

                            # 绘制检测框（如果存在）
                            if detections:
                                # 计算缩放比例
                                scale_x = display_w / decode_w
                                scale_y = display_h / decode_h
                                scale = min(scale_x, scale_y)

                                scaled_detections = []
                                for det in detections:
                                    x1, y1, x2, y2, conf, class_id, class_name = det
                                    scaled_x1 = int(x1 * scale)
                                    scaled_y1 = int(y1 * scale)
                                    scaled_x2 = int(x2 * scale)
                                    scaled_y2 = int(y2 * scale)
                                    scaled_detections.append([scaled_x1, scaled_y1, scaled_x2, scaled_y2, conf, class_id, class_name])

                                img = self.yolo_detector.draw_detections(img, scaled_detections)
                        except Exception as e:
                            print(f"AI检测错误: {e}")
                            import traceback
                            traceback.print_exc()
                    
                    # 限制UI更新频率，防止队列积压
                    if not hasattr(self, '_last_update_time'):
                        self._last_update_time = 0
                    
                    current_time = time.time()
                    # 移除更新频率限制，让系统尽可能快地更新（提高帧率）
                    # 如果出现UI卡顿，可以恢复限制：if current_time - self._last_update_time >= 0.016:  # 60fps
                    try:
                        # 将 PIL Image 放入 UI 队列，由主线程消费并创建 PhotoImage
                        try:
                            # 如果队列已满，先清空旧项以保证最新帧能进去
                            if self._ui_queue.full():
                                try:
                                    _ = self._ui_queue.get_nowait()
                                except Exception:
                                    pass
                            self._ui_queue.put_nowait(img)
                        except Exception:
                            pass
                        # 安排主线程消费队列（after_idle 在主线程执行）
                        try:
                            self.panel1.after_idle(self._consume_ui_queue)
                        except Exception:
                            pass
                        self._last_update_time = current_time
                    except Exception as e:
                        print(f"UI队列放置错误: {e}")
                        import traceback
                        traceback.print_exc()
                    
                    error_count = 0
                    
                    # 定期清理内存（每1000帧清理一次，且帧数必须大于0）
                    if self._frame_count > 0 and self._frame_count % 1000 == 0:
                        import gc
                        gc.collect()
                        print(f"内存清理完成 (帧数: {self._frame_count})")
                except (ValueError, IndexError, OSError, subprocess.TimeoutExpired) as e:
                    print(f"解码异常: {type(e).__name__}: {e}")
                    error_count += 1
                    if error_count > max_error_count:
                        self.need_restart_stream = True
                    # 添加短暂延迟，避免错误循环时CPU占用过高
                    time.sleep(0.001)
                    continue
                except Exception as e:
                    print(f"未知异常: {type(e).__name__}: {e}")
                    import traceback
                    traceback.print_exc()
                    error_count += 1
                    if error_count > max_error_count:
                        self.need_restart_stream = True
                    time.sleep(0.001)
                    continue
                elapsed = time.time() - start_time
                # 动态控制帧率，根据实际FPS调整
                # 如果FPS过高（>60），则适当限制；如果FPS正常，则不限制
                if self._current_fps > 60:
                    target_frame_time = 1.0 / 60.0  # 限制到60fps
                    if elapsed < target_frame_time:
                        time.sleep(target_frame_time - elapsed)
                elif self._current_fps > 0 and self._current_fps < 10:
                    # FPS过低时不sleep，让系统尽可能快地处理
                    pass
                else:
                    # 正常情况：如果处理太快则sleep，避免CPU占用过高
                    target_frame_time = 1.0 / 30.0  # 约33ms
                    if elapsed < target_frame_time:
                        time.sleep(target_frame_time - elapsed)
            
        except Exception as e:
            print(f"流处理异常: {e}")
            def update_error_status():
                self.stream_status.set("播放错误")
                self.status_label.config(fg="#ff6666")
                self.is_playing = False
                # 恢复占位文本
                if not hasattr(self.panel1, 'placeholder') or not self.panel1.placeholder:
                    placeholder = Label(self.panel1.master, text="等待视频流...", 
                                     bg="#000000", fg="#666666", font=('Segoe UI', 12))
                    placeholder.place(relx=0.5, rely=0.5, anchor='center')
                    self.panel1.placeholder = placeholder
            self.panel1.after(0, update_error_status)
        finally:
            # 清理资源
            self._cleanup_ffmpeg_procs()
            if not self.stop_flag:
                def update_stopped_status():
                    self.stream_status.set("已停止")
                    self.status_label.config(fg="#a0a0a0")
                    self.is_playing = False
                self.panel1.after(0, update_stopped_status)

    def _update_panel(self, imgtk):
        """更新视频面板，带错误处理"""
        try:
            # 保存引用防止被垃圾回收
            self.panel1.imgtk = imgtk
            self.panel1.config(image=imgtk)
            self.stream_status.set("播放中")
            self.status_label.config(fg="#00d4aa")
        except Exception as e:
            print(f"更新面板错误: {e}")

    def _consume_ui_queue(self):
        """在主线程消费最新的 PIL Image 并创建 PhotoImage 更新面板"""
        try:
            # Drain queue to get latest image (drop older frames)
            img = None
            dropped_count = 0
            while True:
                try:
                    item = self._ui_queue.get_nowait()
                    if img is not None:
                        dropped_count += 1  # 计数丢弃的旧帧
                    img = item
                except Exception:
                    break

            if img is None:
                return

            # 每丢弃 10+ 帧就打印一次警告（说明有帧积压）
            if dropped_count > 0 and dropped_count % 10 == 0:
                print(f"[诊断] UI 队列消费时丢弃了 {dropped_count} 帧（可能表明处理不过来导致积压）")

            # 在主线程创建 PhotoImage（Tk 相关对象应在主线程创建）
            _photo_start = time.time()
            imgtk = ImageTk.PhotoImage(image=img)
            _photo_end = time.time()
            
            # 诊断打印（频率降低，每 30 次调用打印一次以避免过多日志）
            if not hasattr(self, '_ui_consume_count'):
                self._ui_consume_count = 0
            self._ui_consume_count += 1
            if self._ui_consume_count % 30 == 0:
                print(f"PhotoImage创建时间: {(_photo_end - _photo_start)*1000:.1f} ms, 丢弃帧数: {dropped_count}")
            
            # 直接更新面板
            self._update_panel(imgtk)
        except Exception as e:
            print(f"UI 队列消费错误: {e}")

    def create_ptz_controls(self):
        """创建PTZ控制面板 - PotPlayer 风格"""
        # 右侧控制面板容器 - 固定宽度，防止被挤压
        self.right_panel = ttk.Frame(self)
        self.right_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=0, pady=0)
        # 设置固定宽度和最小宽度
        self.right_panel.config(width=260)
        self.right_panel.pack_propagate(False)  # 防止子组件改变父组件大小
        
        right_panel = self.right_panel  # 使用局部变量以便后续代码使用
        
        # ONVIF配置面板 - PotPlayer 风格（紧凑、扁平）
        config_frame = ttk.LabelFrame(right_panel, text="ONVIF 连接")
        config_frame.pack(fill=tk.X, pady=(5, 5), padx=5)

        # 输入控件 - PotPlayer 风格（紧凑布局）
        input_grid = ttk.Frame(config_frame)
        input_grid.pack(fill=tk.X, padx=8, pady=8)
        
        # IP地址
        ttk.Label(input_grid, text="IP:", font=('Segoe UI', 8)).grid(row=0, column=0, sticky=tk.W, pady=3)
        self.ip_entry = ttk.Entry(input_grid, width=20)
        self.ip_entry.insert(0, "172.20.4.99")
        self.ip_entry.grid(row=0, column=1, padx=(5, 0), pady=3, sticky=tk.EW)
        
        # 端口
        ttk.Label(input_grid, text="端口:", font=('Segoe UI', 8)).grid(row=1, column=0, sticky=tk.W, pady=3)
        self.port_entry = ttk.Entry(input_grid, width=20)
        self.port_entry.insert(0, "1234")
        self.port_entry.grid(row=1, column=1, padx=(5, 0), pady=3, sticky=tk.EW)
        
        # 用户名
        ttk.Label(input_grid, text="用户:", font=('Segoe UI', 8)).grid(row=2, column=0, sticky=tk.W, pady=3)
        self.user_entry = ttk.Entry(input_grid, width=20)
        self.user_entry.insert(0, "admin")
        self.user_entry.grid(row=2, column=1, padx=(5, 0), pady=3, sticky=tk.EW)
        
        # 密码
        ttk.Label(input_grid, text="密码:", font=('Segoe UI', 8)).grid(row=3, column=0, sticky=tk.W, pady=3)
        self.pwd_entry = ttk.Entry(input_grid, width=20, show="*")
        self.pwd_entry.insert(0, "admin")
        self.pwd_entry.grid(row=3, column=1, padx=(5, 0), pady=3, sticky=tk.EW)
        
        input_grid.columnconfigure(1, weight=1)

        # 连接按钮和状态 - PotPlayer 风格
        btn_frame = ttk.Frame(config_frame)
        btn_frame.pack(fill=tk.X, padx=8, pady=(0, 5))
        connect_btn = ttk.Button(btn_frame, text="连接", 
                                command=self.connect_onvif, style='Connect.TButton')
        connect_btn.pack(fill=tk.X)
        
        # 连接状态
        status_frame = ttk.Frame(config_frame)
        status_frame.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Label(status_frame, text="状态:", font=('Segoe UI', 8)).pack(side=tk.LEFT)
        self.status_indicator = tk.Label(status_frame, textvariable=self.connection_status,
                                        bg="#2a2a2a", fg="#ff6666", font=('Segoe UI', 8, 'bold'))
        self.status_indicator.pack(side=tk.LEFT, padx=(5, 0))

        # PTZ 控制（移动到 ONVIF 配置下方）
        control_frame = ttk.LabelFrame(right_panel, text="PTZ 控制")
        control_frame.pack(fill=tk.X, pady=(0, 5), padx=5)

        # 步长设置 - 紧凑布局
        step_frame = ttk.Frame(control_frame)
        step_frame.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(step_frame, text="步长:", font=('Segoe UI', 8)).pack(side=tk.LEFT)
        self.step_var = tk.IntVar(value=10)
        step_entry = ttk.Entry(step_frame, textvariable=self.step_var, width=8)
        step_entry.pack(side=tk.LEFT, padx=(5, 0))
        ttk.Label(step_frame, text="(1-10000)", font=('Segoe UI', 7), 
                 foreground="#888888").pack(side=tk.LEFT, padx=(3, 0))

        # 方向控制 - PotPlayer 风格（紧凑按钮）
        direction_frame = ttk.Frame(control_frame)
        direction_frame.pack(padx=8, pady=6)
        
        ttk.Label(direction_frame, text="方向", font=('Segoe UI', 8)).grid(
            row=0, column=0, columnspan=3, pady=(0, 5))
        
        # 创建方向按钮网格 - 更小的按钮
        btn_up = ttk.Button(direction_frame, text="▲", 
                           command=lambda: self.move_camera(0, -self.get_step()),
                           style='Control.TButton', width=3)
        btn_up.grid(row=1, column=1, padx=2, pady=2)
        
        btn_left = ttk.Button(direction_frame, text="◄", 
                             command=lambda: self.move_camera(-self.get_step(), 0),
                             style='Control.TButton', width=3)
        btn_left.grid(row=2, column=0, padx=2, pady=2)
        
        btn_center = ttk.Button(direction_frame, text="●", 
                               command=lambda: self.move_camera(0, 0),
                               style='Control.TButton', width=3)
        btn_center.grid(row=2, column=1, padx=2, pady=2)
        
        btn_right = ttk.Button(direction_frame, text="►", 
                              command=lambda: self.move_camera(self.get_step(), 0),
                              style='Control.TButton', width=3)
        btn_right.grid(row=2, column=2, padx=2, pady=2)
        
        btn_down = ttk.Button(direction_frame, text="▼", 
                             command=lambda: self.move_camera(0, self.get_step()),
                             style='Control.TButton', width=3)
        btn_down.grid(row=3, column=1, padx=2, pady=2)

        # 原智能识别面板已移动到检测结果区域（合并）

        # 原 PTZ 面板已移动到 ONVIF 配置下方

        # 变焦控制 - PotPlayer 风格
        zoom_frame = ttk.Frame(control_frame)
        zoom_frame.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Label(zoom_frame, text="变焦", font=('Segoe UI', 8)).pack(anchor=tk.W, pady=(0, 3))
        
        zoom_btn_frame = ttk.Frame(zoom_frame)
        zoom_btn_frame.pack(fill=tk.X)
        ttk.Button(zoom_btn_frame, text="+ 放大", 
                  command=lambda: self.zoom_camera(0.1),
                  style='Small.TButton').pack(side=tk.LEFT, padx=(0, 3), fill=tk.X, expand=True)
        ttk.Button(zoom_btn_frame, text="- 缩小", 
                  command=lambda: self.zoom_camera(-0.1),
                  style='Small.TButton').pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 合并智能识别与检测结果（位于原检测结果位置）
        ai_detect_frame = ttk.LabelFrame(right_panel, text="智能识别 / 检测结果")
        ai_detect_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 5), padx=5)

        # 上部：智能识别配置
        ai_top = ttk.Frame(ai_detect_frame)
        ai_top.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(ai_top, text="识别类型:", font=('Segoe UI', 8)).pack(anchor=tk.W)

        # 人 / 车 / 无人机 复选
        types_row = ttk.Frame(ai_top)
        types_row.pack(fill=tk.X, pady=(4, 6))
        person_check = tk.Checkbutton(types_row, text="人", variable=self.detect_person,
                                      bg="#2a2a2a", fg="#e0e0e0", selectcolor="#2a2a2a",
                                      activebackground="#2a2a2a", activeforeground="#00d4aa",
                                      font=('Segoe UI', 8))
        person_check.pack(side=tk.LEFT, padx=(0,8))
        car_check = tk.Checkbutton(types_row, text="车辆", variable=self.detect_car,
                       bg="#2a2a2a", fg="#e0e0e0", selectcolor="#2a2a2a",
                       activebackground="#2a2a2a", activeforeground="#00d4aa",
                       font=('Segoe UI', 8))
        car_check.pack(side=tk.LEFT, padx=(0,8))
        drone_check = tk.Checkbutton(types_row, text="无人机", variable=self.detect_drone,
                                     bg="#2a2a2a", fg="#e0e0e0", selectcolor="#2a2a2a",
                                     activebackground="#2a2a2a", activeforeground="#00d4aa",
                                     font=('Segoe UI', 8))
        drone_check.pack(side=tk.LEFT)

        # 置信度阈值
        conf_frame = ttk.Frame(ai_top)
        conf_frame.pack(fill=tk.X, pady=(0,6))
        ttk.Label(conf_frame, text="置信度:", font=('Segoe UI', 8)).pack(side=tk.LEFT)
        self.conf_threshold = tk.DoubleVar(value=0.25)
        conf_scale = ttk.Scale(conf_frame, from_=0.1, to=0.9, variable=self.conf_threshold, 
                              orient=tk.HORIZONTAL, length=120)
        conf_scale.pack(side=tk.LEFT, padx=(6,6))
        self.conf_label = tk.Label(conf_frame, text="0.25", bg="#2a2a2a", fg="#e0e0e0", 
                                   font=('Segoe UI', 8), width=4)
        self.conf_label.pack(side=tk.LEFT)
        conf_scale.configure(command=lambda v: self.conf_label.config(text=f"{float(v):.2f}"))

        # 下部：检测结果显示
        results_container = ttk.Frame(ai_detect_frame)
        results_container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.detection_text_widget = ScrolledText(
            results_container, height=10, width=28,
            bg="#1a1a1a", fg="#e0e0e0", font=('Consolas', 8), wrap=tk.WORD, relief=tk.FLAT, borderwidth=1
        )
        self.detection_text_widget.pack(fill=tk.BOTH, expand=True)
        self.detection_text_widget.insert('1.0', '等待检测...\n')
        self.detection_text_widget.config(state=tk.DISABLED)

        # 清空按钮
        clear_btn_frame = ttk.Frame(ai_detect_frame)
        clear_btn_frame.pack(fill=tk.X, padx=5, pady=(0, 5))
        clear_btn = ttk.Button(clear_btn_frame, text="清空", command=self.clear_detection_results, style='Small.TButton')
        clear_btn.pack(side=tk.RIGHT)

    def clear_detection_results(self):
        """清空检测结果显示"""
        if self.detection_text_widget:
            self.detection_text_widget.config(state=tk.NORMAL)
            self.detection_text_widget.delete('1.0', tk.END)
            self.detection_text_widget.insert('1.0', '等待检测...\n')
            self.detection_text_widget.config(state=tk.DISABLED)
        self.detection_results = []
    
    def update_detection_display(self, detections, frame_width, frame_height):
        """更新检测结果显示"""
        if not self.detection_text_widget:
            return
        
        # 防止除零错误
        if frame_width <= 0 or frame_height <= 0:
            return
        
        try:
            self.detection_text_widget.config(state=tk.NORMAL)
            self.detection_text_widget.delete('1.0', tk.END)
            
            if not detections:
                self.detection_text_widget.insert('1.0', '未检测到目标\n')
            else:
                # 显示检测结果数量
                self.detection_text_widget.insert('1.0', f'检测到 {len(detections)} 个目标:\n\n')
                
                # 显示每个目标的详细信息
                for idx, detection in enumerate(detections, 1):
                    try:
                        # 安全解包，防止数据格式错误
                        if len(detection) < 7:
                            continue
                        x1, y1, x2, y2, conf, class_id, class_name = detection
                        
                        # 边界检查
                        x1, y1, x2, y2 = max(0, int(x1)), max(0, int(y1)), max(0, int(x2)), max(0, int(y2))
                        
                        # 计算中心点位置（相对于画面尺寸的百分比）
                        center_x = (x1 + x2) / 2.0
                        center_y = (y1 + y2) / 2.0
                        center_x_pct = (center_x / frame_width) * 100
                        center_y_pct = (center_y / frame_height) * 100
                        
                        # 计算边界框尺寸
                        bbox_width = max(0, x2 - x1)
                        bbox_height = max(0, y2 - y1)
                        bbox_width_pct = (bbox_width / frame_width) * 100
                        bbox_height_pct = (bbox_height / frame_height) * 100
                        
                        # 格式化显示信息
                        info = f"[{idx}] {class_name}\n"
                        info += f"  置信度: {conf:.2%}\n"
                        info += f"  位置: ({x1}, {y1}) - ({x2}, {y2})\n"
                        info += f"  中心: ({center_x:.0f}, {center_y:.0f})\n"
                        info += f"  中心%: ({center_x_pct:.1f}%, {center_y_pct:.1f}%)\n"
                        info += f"  尺寸: {bbox_width}x{bbox_height}\n"
                        info += f"  尺寸%: ({bbox_width_pct:.1f}%, {bbox_height_pct:.1f}%)\n"
                        info += "\n"
                        
                        self.detection_text_widget.insert(tk.END, info)
                    except (ValueError, IndexError, TypeError) as e:
                        print(f"处理检测结果错误: {e}, detection: {detection}")
                        continue
            
            self.detection_text_widget.config(state=tk.DISABLED)
            # 滚动到顶部
            self.detection_text_widget.see('1.0')
            
        except Exception as e:
            print(f"更新检测结果显示错误: {e}")
            import traceback
            traceback.print_exc()

    def log_onvif(self, send_content, recv_content):
        """把ONVIF发送和接收的内容记录到界面上的两个滚动文本框。

        该方法可能由后台线程调用，使用 `after` 确保在主线程更新 UI。
        """
        try:
            ts = time.strftime('%Y-%m-%d %H:%M:%S')
            send_block = f"[{ts}] SEND:\n{send_content}\n\n" if send_content is not None else f"[{ts}] SEND: <empty>\n\n"
            recv_block = f"[{ts}] RECV:\n{recv_content}\n\n" if recv_content is not None else f"[{ts}] RECV: <empty>\n\n"

            def _append():
                try:
                    if hasattr(self, 'send_text') and self.send_text:
                        try:
                            self.send_text.config(state=tk.NORMAL)
                            self.send_text.insert(tk.END, send_block)
                            self.send_text.see(tk.END)
                            self.send_text.config(state=tk.DISABLED)
                        except Exception:
                            pass
                    else:
                        print(send_block)

                    if hasattr(self, 'recv_text') and self.recv_text:
                        try:
                            self.recv_text.config(state=tk.NORMAL)
                            self.recv_text.insert(tk.END, recv_block)
                            self.recv_text.see(tk.END)
                            self.recv_text.config(state=tk.DISABLED)
                        except Exception:
                            pass
                    else:
                        print(recv_block)
                except Exception as e:
                    print(f"log_onvif UI 更新失败: {e}")

            try:
                # 在主线程安全地更新UI
                if hasattr(self, 'parent') and getattr(self, 'parent') is not None:
                    self.parent.after(0, _append)
                else:
                    _append()
            except Exception:
                _append()
        except Exception as e:
            print(f"log_onvif 失败: {e}")

    def _detect_worker(self):
        """后台检测线程：从队列获取下采样帧并运行YOLO检测，将结果缩放回解码分辨率后写入 self._last_detections"""
        try:
            while True:
                try:
                    if self._detect_queue is None:
                        time.sleep(0.1)
                        continue
                    item = self._detect_queue.get()
                    if not item:
                        continue
                    (frame_np, target_classes, conf_threshold, target_detect_size, scale_back, decode_w, decode_h) = item
                    # 执行检测（这是阻塞操作，但在单独线程中）
                    results = []
                    try:
                        results = self.yolo_detector.detect(frame_np, conf_threshold=conf_threshold, target_classes=target_classes, imgsz=int(target_detect_size))
                    except Exception as e:
                        print(f"检测线程内部检测错误: {e}")
                        results = []

                    # 将检测框坐标映射回解码分辨率
                    mapped = []
                    try:
                        for det in results:
                            x1, y1, x2, y2, conf, class_id, class_name = det
                            mx1 = int(x1 * scale_back)
                            my1 = int(y1 * scale_back)
                            mx2 = int(x2 * scale_back)
                            my2 = int(y2 * scale_back)
                            # 做边界裁剪以防越界
                            mx1 = max(0, min(mx1, decode_w - 1))
                            my1 = max(0, min(my1, decode_h - 1))
                            mx2 = max(0, min(mx2, decode_w - 1))
                            my2 = max(0, min(my2, decode_h - 1))
                            mapped.append([mx1, my1, mx2, my2, conf, class_id, class_name])
                    except Exception:
                        mapped = []

                    # 更新共享检测结果
                    try:
                        with self.ai_lock:
                            self._last_detections = mapped
                    except Exception:
                        self._last_detections = mapped

                    # 更新检测结果显示（在主线程）
                    try:
                        if hasattr(self, 'panel1') and getattr(self, 'panel1') is not None:
                            # 使用解码分辨率作为参数
                            self.panel1.after(0, self.update_detection_display, mapped, decode_w, decode_h)
                    except Exception:
                        pass
                except Exception:
                    time.sleep(0.01)
                    continue
        except Exception as e:
            print(f"检测线程退出: {e}")

    def _schedule_restart(self, reason=None):
        """计划一次重启，使用指数退避来避免频繁重启"""
        try:
            self._restart_attempts = getattr(self, '_restart_attempts', 0) + 1
            backoff = min(self._max_backoff, 2 ** (self._restart_attempts - 1))
            self._next_restart_time = time.time() + backoff
            self.need_restart_stream = True
            print(f"计划重启流（原因: {reason}），尝试次数: {self._restart_attempts}, 回退: {backoff}s")
            # 如果近期一直使用 CUDA 且多次触发重启，自动回退到软件解码
            try:
                last_hw = getattr(self, '_last_hw_accel', None)
                if last_hw and 'CUDA' in str(last_hw).upper():
                    self._cuda_failures = getattr(self, '_cuda_failures', 0) + 1
                else:
                    # 非CUDA引起的不计入CUDA失败次数
                    self._cuda_failures = 0
                if getattr(self, '_cuda_failures', 0) >= getattr(self, '_cuda_disable_threshold', 3):
                    self._cuda_disabled = True
                    print(f"检测到连续 {self._cuda_failures} 次由 CUDA 导致的重启，已自动回退到软件解码模式")
            except Exception:
                pass
        except Exception as e:
            print(f"计划重启失败: {e}")

    def _toggle_hw_accel(self):
        """UI回调：切换硬件解码开关并计划重启以应用新设置"""
        try:
            enabled = bool(self.hw_accel_var.get())
            # _cuda_disabled is True when hardware is disabled
            self._cuda_disabled = not enabled
            print(f"用户切换硬件解码: {'启用' if enabled else '禁用'}")
            # 如果正在播放，计划重启使设置生效；否则下次启动生效
            if self.is_playing:
                self._schedule_restart(reason='user toggle hw accel')
        except Exception as e:
            print(f"切换硬件解码失败: {e}")

    def get_step(self):
        """获取步长，范围限制在1~10000，并归一化到0~1"""
        try:
            step = self.step_var.get()
            step = max(1, min(10000, step))
            return step / 10000.0  # 步长归一化到0~1
        except Exception:
            return 0.01

    def connect_onvif(self):
        """连接ONVIF摄像机"""
        try:
            ip = self.ip_entry.get()
            port = int(self.port_entry.get())
            username = self.user_entry.get()
            password = self.pwd_entry.get()
            
            # 空值校验
            if not all([ip, port, username, password]):
                raise ValueError("请填写所有必填项")
            
            self.connection_status.set("连接中...")
            self.status_indicator.config(fg="#ffaa00")
            self.parent.update()
                
            self.onvif_controller = ONVIFController(ip, port, username, password)
            self.connection_status.set("已连接")
            self.status_indicator.config(fg="#00d4aa")
            messagebox.showinfo("成功", "摄像机连接成功！")
        except Exception as e:
            self.connection_status.set("连接失败")
            self.status_indicator.config(fg="#ff6666")
            messagebox.showerror("错误", f"连接失败: {str(e)}")    

    def move_camera(self, pan, tilt):
        """移动摄像机"""
        if self.onvif_controller:
            send, recv = self.onvif_controller.relative_move_with_log(pan, tilt, 0)
            self.log_onvif(send, recv)
    
    def zoom_camera(self, zoom):
        """变焦控制"""
        if self.onvif_controller:
            send, recv = self.onvif_controller.relative_move_with_log(0, 0, zoom)
            self.log_onvif(send, recv)
    
    def toggle_ai_mode(self):
        """切换智能模式"""
        if self.ai_mode_enabled.get():
            # 启用智能模式
            if not YOLO_AVAILABLE:
                messagebox.showerror("错误", "YOLO未安装！\n请运行: pip install ultralytics")
                self.ai_mode_enabled.set(False)
                return
            
            if self.yolo_detector is None:
                try:
                    # 尝试启用 GPU，如果可用则在 YOLODetector 内部生效
                    self.yolo_detector = YOLODetector(device='cuda', use_fp16=True)
                    if not self.yolo_detector.is_loaded:
                        messagebox.showerror("错误", "YOLO模型加载失败！")
                        self.ai_mode_enabled.set(False)
                        self.yolo_detector = None
                        return
                    # 初始化异步检测队列与后台线程（如果尚未启动）
                    try:
                        if self._detect_queue is None:
                            self._detect_queue = queue.Queue(maxsize=1)
                        if self._detect_thread is None or not getattr(self._detect_thread, 'is_alive', lambda: False)():
                            self._detect_thread = Thread(target=self._detect_worker, daemon=True)
                            self._detect_thread.start()
                    except Exception:
                        pass
                except Exception as e:
                    messagebox.showerror("错误", f"初始化YOLO失败: {str(e)}")
                    self.ai_mode_enabled.set(False)
                    self.yolo_detector = None
                    return
            print("智能模式已启用")
        else:
            print("智能模式已禁用")