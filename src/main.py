"""
RTSP视频播放器主程序入口
"""
import sys
import os

# 将项目根目录添加到Python路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import tkinter as tk
from src.gui.player_window import PlayerWindow


def main():
    """主函数"""
    root = tk.Tk()
    root.title("RTSP 视频播放器 - 专业版")
    root.geometry("1920x1080")  # 默认窗口大小，内部解码分辨率为 2560x1440（2K）
    root.update()  # 强制刷新窗口尺寸
    root.minsize(1000, 600)  # 设置最小窗口大小，确保右侧面板有足够空间
    # 不设置 maxsize，允许用户调整到全屏或更大尺寸
    
    # 设置窗口图标（如果有的话）
    try:
        root.iconbitmap('icon.ico')
    except:
        pass
    
    player_window = PlayerWindow(root)
    player_window.pack(fill=tk.BOTH, expand=True)
    
    # 确保右侧面板始终可见
    def ensure_right_panel_visible(event=None):
        if hasattr(player_window, 'right_panel') and player_window.right_panel:
            # 强制更新布局，确保右侧面板显示
            player_window.right_panel.update_idletasks()
    
    # 窗口关闭时的清理函数
    def on_closing():
        """窗口关闭时清理所有资源"""
        # 停止播放
        if player_window.is_playing:
            player_window.stop_stream()
            # 等待线程结束
            if player_window.stream_thread and player_window.stream_thread.is_alive():
                player_window.stream_thread.join(timeout=2)
        # 清理所有FFmpeg进程
        player_window._cleanup_ffmpeg_procs()
        # 关闭窗口
        root.destroy()
    
    # 绑定窗口关闭事件
    root.protocol("WM_DELETE_WINDOW", on_closing)
    # 绑定窗口大小变化事件
    root.bind('<Configure>', ensure_right_panel_visible)
    
    root.mainloop()


if __name__ == "__main__":
    main()
