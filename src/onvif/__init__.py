"""
ONVIF控制模块
"""
# 延迟导入以避免循环导入问题
def _get_onvif_controller():
    from .onvif_controller import ONVIFController
    return ONVIFController

# 使用延迟导入的方式
import sys
_this_module = sys.modules[__name__]

def __getattr__(name):
    if name == 'ONVIFController':
        ONVIFController = _get_onvif_controller()
        setattr(_this_module, 'ONVIFController', ONVIFController)
        return ONVIFController
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

__all__ = ['ONVIFController']


