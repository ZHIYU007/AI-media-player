"""
ONVIF摄像机控制模块
提供PTZ（平移、倾斜、变焦）控制功能
"""
import time
import importlib, sys, os
from zeep import helpers
from lxml import etree
from zeep.plugins import HistoryPlugin

ONVIFCamera = None
_onvif_import_error = None

try:
    # 临时从 sys.path 中移除项目内的 src 路径，避免本地 src/onvif 覆盖已安装包
    this_dir = os.path.abspath(os.path.dirname(__file__))
    project_src_dir = os.path.abspath(os.path.join(this_dir, '..'))  # src/
    removed_entries = []
    for p in list(sys.path):
        try:
            if p and os.path.abspath(p).startswith(project_src_dir):
                removed_entries.append((p, sys.path.index(p)))
                sys.path.remove(p)
        except Exception:
            continue
    try:
        # 现在尝试导入已安装的 onvif 包（来自 site-packages）
        onvif_mod = importlib.import_module('onvif')
        # 常见位置： onvif.ONVIFCamera 或 onvif.client.ONVIFCamera
        if hasattr(onvif_mod, 'ONVIFCamera'):
            ONVIFCamera = onvif_mod.ONVIFCamera
        elif hasattr(onvif_mod, 'client') and hasattr(onvif_mod.client, 'ONVIFCamera'):
            ONVIFCamera = onvif_mod.client.ONVIFCamera
        else:
            # 再尝试 explicit submodule
            try:
                mod_client = importlib.import_module('onvif.client')
                if hasattr(mod_client, 'ONVIFCamera'):
                    ONVIFCamera = mod_client.ONVIFCamera
            except Exception:
                pass
    finally:
        # 恢复 sys.path（保持原顺序）
        for p, idx in reversed(removed_entries):
            try:
                sys.path.insert(idx, p)
            except Exception:
                if p not in sys.path:
                    sys.path.append(p)

    if ONVIFCamera is None:
        raise ImportError("已安装的 onvif 包中未找到 ONVIFCamera")

except Exception as e:
    _onvif_import_error = e
    # 降级：定义占位控制器，确保程序能启动并在 UI 提示
    class ONVIFController:
        def __init__(self, *args, **kwargs):
            self.available = False
            self.history = None

        def get_profiles(self):
            return []

        def relative_move_with_log(self, pan, tilt, zoom, speed=0.5):
            send = "onvif-zeep 未正确导入或被本地 src/onvif 覆盖。请确认已安装：pip install --upgrade onvif-zeep；或将项目内的 src/onvif 重命名。"
            recv = "未执行"
            return send, recv

        def relative_move(self, pan, tilt, zoom, speed=0.5):
            return

        def absolute_move(self, pan, tilt, zoom, speed=0.5):
            return

        def continuous_move(self, pan, tilt, zoom, timeout=1):
            return

    print("警告：未能导入第三方 onvif 包，已启用占位 ONVIFController。错误：", _onvif_import_error)

class ONVIFController:
    """ONVIF摄像机控制器"""
    def __init__(self, ip, port, username, password):
        self.history = HistoryPlugin()
        self.cam = ONVIFCamera(ip, port, username, password)
        self.ptz = self.cam.create_ptz_service()
        self.media = self.cam.create_media_service()
        self.imaging = self.cam.create_imaging_service()
        # 兼容主流onvif-py，插件加到_client.plugins
        try:
            self.ptz._client.plugins.append(self.history)
        except AttributeError:
            pass
        try:
            self.media._client.plugins.append(self.history)
        except AttributeError:
            pass
        try:
            self.imaging._client.plugins.append(self.history)
        except AttributeError:
            pass
        
    def get_profiles(self):
        """获取摄像机配置集"""
        try:
            profiles = self.media.GetProfiles()
            if not profiles:
                raise ValueError("未找到可用的配置集")
            return profiles
        except Exception as e:
            print(f"获取配置集失败: {e}")
            raise
    
    def absolute_move(self, pan, tilt, zoom, speed=0.5):
        """绝对移动"""
        try:
            profiles = self.get_profiles()
            if not profiles:
                raise ValueError("未找到可用的配置集")
            req = self.ptz.create_type('AbsoluteMove')
            req.ProfileToken = profiles[0].token
            req.Position = {
                'PanTilt': {'x': pan, 'y': tilt},
                'Zoom': {'x': zoom}
            }
            req.Speed = {
                'PanTilt': {'x': speed, 'y': speed},
                'Zoom': {'x': speed}
            }
            self.ptz.AbsoluteMove(req)
        except Exception as e:
            print(f"绝对移动失败: {e}")
            raise
    
    def relative_move(self, pan, tilt, zoom, speed=0.5):
        """相对移动"""
        try:
            profiles = self.get_profiles()
            if not profiles:
                raise ValueError("未找到可用的配置集")
            req = self.ptz.create_type('RelativeMove')
            req.ProfileToken = profiles[0].token
            req.Translation = {
                'PanTilt': {'x': pan, 'y': tilt},
                'Zoom': {'x': zoom}
            }
            req.Speed = {
                'PanTilt': {'x': speed, 'y': speed},
                'Zoom': {'x': speed}
            }
            self.ptz.RelativeMove(req)
        except Exception as e:
            print(f"相对移动失败: {e}")
            raise
    
    def continuous_move(self, pan, tilt, zoom, timeout=1):
        """持续移动"""
        try:
            profiles = self.get_profiles()
            if not profiles:
                raise ValueError("未找到可用的配置集")
            req = self.ptz.create_type('ContinuousMove')
            req.ProfileToken = profiles[0].token
            req.Velocity = {
                'PanTilt': {'x': pan, 'y': tilt},
                'Zoom': {'x': zoom}
            }
            self.ptz.ContinuousMove(req)
            time.sleep(timeout)
            self.ptz.Stop({'ProfileToken': req.ProfileToken})
        except Exception as e:
            print(f"持续移动失败: {e}")
            raise

    def relative_move_with_log(self, pan, tilt, zoom, speed=0.5):
        """相对移动并记录日志"""
        try:
            profiles = self.get_profiles()
            if not profiles:
                raise ValueError("未找到可用的配置集")
            req = self.ptz.create_type('RelativeMove')
            req.ProfileToken = profiles[0].token
            req.Translation = {
                'PanTilt': {'x': pan, 'y': tilt},
                'Zoom': {'x': zoom}
            }
            req.Speed = {
                'PanTilt': {'x': speed, 'y': speed},
                'Zoom': {'x': speed}
            }
            send_content = "未捕获到发送内容"
            recv_content = "未捕获到返回内容"
            try:
                self.ptz.RelativeMove(req)
                # 捕获最近一次请求和响应
                if hasattr(self.history, 'last_sent') and self.history.last_sent is not None:
                    try:
                        send_content = etree.tostring(self.history.last_sent["envelope"], pretty_print=True, encoding='unicode')
                    except (AttributeError, KeyError, etree.Error) as e:
                        send_content = f"解析发送内容失败: {e}"
                if hasattr(self.history, 'last_received') and self.history.last_received is not None:
                    try:
                        recv_content = etree.tostring(self.history.last_received["envelope"], pretty_print=True, encoding='unicode')
                    except (AttributeError, KeyError, etree.Error) as e:
                        recv_content = f"解析返回内容失败: {e}"
            except Exception as e:
                recv_content = f"Error: {e}"
                raise
            print("发送内容：", send_content)
            print("返回内容：", recv_content)
            print("history.last_sent:", getattr(self.history, 'last_sent', None))
            print("history.last_received:", getattr(self.history, 'last_received', None))
            return send_content, recv_content
        except Exception as e:
            print(f"相对移动失败: {e}")
            raise


