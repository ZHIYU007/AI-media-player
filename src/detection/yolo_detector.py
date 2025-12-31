"""
YOLO目标检测器模块
提供基于YOLO模型的目标检测和绘制功能
"""
from threading import Lock
from PIL import Image, ImageDraw, ImageFont

# YOLO相关导入（可选，如果未安装则使用占位实现）
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False
    print("警告: ultralytics未安装，智能模式将不可用。请运行: pip install ultralytics")


class YOLODetector:
    """YOLO目标检测器

    支持可选的 `device` 参数以指定推理设备（例如 'cuda' 或 'cpu'）。
    当 device=None 时，ultralytics 会自动选择可用设备（优先 GPU）。
    """
    def __init__(self, model_path=None, device=None, use_fp16=True):
        self.model = None
        self.lock = Lock()
        self.is_loaded = False
        self.device = device
        self.use_fp16 = use_fp16

        if not YOLO_AVAILABLE:
            print("YOLO不可用，智能模式将无法使用")
            return

        try:
            # 如果没有提供模型路径，使用默认的YOLOv8模型（会自动下载）
            if model_path is None:
                model_path = 'yolov8n.pt'  # nano版本，速度快

            # 优先传入 device 到模型（ultralytics 会尝试使用它）
            if self.device:
                try:
                    self.model = YOLO(model_path)
                    # 尝试将模型移动到CUDA
                    if 'cuda' in str(self.device).lower():
                        try:
                            import torch
                            if torch.cuda.is_available():
                                try:
                                    self.model.to('cuda')
                                    print("YOLO: 模型已移动到 CUDA")
                                except Exception:
                                    # 有时候 ultralytics 的 YOLO 对象不支持 to('cuda')，忽略
                                    pass
                        except Exception:
                            pass
                    else:
                        # 如果显式要求 CPU，尽量设置为 CPU
                        try:
                            self.model.to('cpu')
                        except Exception:
                            pass
                except Exception:
                    # 兜底：直接由 ultralytics 自动选择设备
                    self.model = YOLO(model_path)
            else:
                # 让 ultralytics 自动选择设备（优先 GPU）
                self.model = YOLO(model_path)

            # 尝试启用半精度以提升推理吞吐（仅当GPU可用且支持时）
            if self.use_fp16:
                try:
                    import torch
                    if torch.cuda.is_available():
                        # 部分 ultralytics 版本允许 model.model.half()
                        try:
                            if hasattr(self.model, 'model') and hasattr(self.model.model, 'half'):
                                self.model.model.half()
                                print('YOLO: 已尝试启用 fp16 (half)')
                        except Exception:
                            pass
                except Exception:
                    pass

            self.is_loaded = True
            print(f"YOLO模型加载成功: {model_path}")
            try:
                print(f"模型支持的类别数量: {len(self.model.names)}")
                print(f"前10个类别: {list(self.model.names.values())[:10]}")
            except Exception:
                pass
        except Exception as e:
            print(f"YOLO模型加载失败: {e}")
            import traceback
            traceback.print_exc()
            self.is_loaded = False
    
    def detect(self, frame, conf_threshold=0.25, target_classes=None, imgsz=640):
        """
        检测目标
        Args:
            frame: numpy数组，形状为(H, W, 3)，RGB格式
            conf_threshold: 置信度阈值
            target_classes: 要检测的类别列表，如['person', 'car', 'drone']，None表示检测所有类别
            imgsz: 推理时的图像尺寸，越小速度越快但精度可能降低（默认640）
        Returns:
            results: 检测结果列表，每个结果包含 [x1, y1, x2, y2, conf, class_id, class_name]
        """
        if not self.is_loaded or self.model is None:
            return []
        
        with self.lock:
            try:
                # YOLO推理，使用较小的推理尺寸提高速度
                # imgsz参数控制推理时的图像尺寸，640是平衡速度和精度的好选择
                # device参数：不指定则自动选择（优先GPU，如果不可用则使用CPU）
                # 如果强制使用GPU，可以设置 device='0'，但需要确保CUDA可用
                results = self.model(frame, conf=conf_threshold, verbose=False, imgsz=imgsz)
                
                detections = []
                all_detected_classes = set()  # 用于调试：记录所有检测到的类别
                
                for result in results:
                    boxes = result.boxes
                    for box in boxes:
                        # 获取边界框坐标
                        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                        # 获取置信度
                        conf = float(box.conf[0].cpu().numpy())
                        # 获取类别ID和名称
                        class_id = int(box.cls[0].cpu().numpy())
                        class_name = self.model.names[class_id]
                        all_detected_classes.add(class_name)
                        
                        # 如果指定了目标类别，只返回匹配的检测结果
                        if target_classes is None:
                            # 检测所有类别
                            detections.append([int(x1), int(y1), int(x2), int(y2), conf, class_id, class_name])
                        else:
                            # 检查类别是否匹配（不区分大小写）
                            target_lower = [c.lower() for c in target_classes]
                            if class_name.lower() in target_lower:
                                detections.append([int(x1), int(y1), int(x2), int(y2), conf, class_id, class_name])
                
                # 调试信息：如果指定了类别但没有匹配的检测结果
                if target_classes and not detections and all_detected_classes:
                    print(f"提示: 检测到类别 {list(all_detected_classes)}，但未匹配目标类别 {target_classes}")
                    print(f"可用类别: {list(self.model.names.values())}")
                
                return detections
            except Exception as e:
                print(f"YOLO检测错误: {e}")
                import traceback
                traceback.print_exc()
                return []
    
    def draw_detections(self, frame, detections, colors=None):
        """
        在帧上绘制检测框
        Args:
            frame: PIL Image对象
            detections: 检测结果列表
            colors: 类别颜色字典，如 {'person': (255, 0, 0), 'car': (0, 255, 0)}
        Returns:
            annotated_frame: 绘制了检测框的PIL Image对象
        """
        if not detections:
            return frame
        
        # 默认颜色
        if colors is None:
            colors = {
                'person': (255, 0, 0),      # 红色
                'car': (0, 255, 0),         # 绿色
                'truck': (0, 255, 255),     # 黄色
                'bus': (255, 165, 0),       # 橙色
                'motorcycle': (255, 0, 255), # 紫色
                'bicycle': (0, 0, 255),     # 蓝色
                'drone': (255, 255, 0),     # 青色
            }
        
        draw = ImageDraw.Draw(frame)
        
        # 尝试加载字体
        try:
            font = ImageFont.truetype("arial.ttf", 16)
        except:
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
            except:
                font = ImageFont.load_default()
        
        for x1, y1, x2, y2, conf, class_id, class_name in detections:
            # 获取颜色
            color = colors.get(class_name.lower(), (255, 255, 255))
            
            # 绘制边界框
            draw.rectangle([x1, y1, x2, y2], outline=color, width=2)
            
            # 绘制标签背景
            label = f"{class_name} {conf:.2f}"
            bbox = draw.textbbox((0, 0), label, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            
            # 标签背景
            draw.rectangle([x1, y1 - text_height - 4, x1 + text_width + 4, y1], 
                          fill=color, outline=color)
            
            # 标签文字
            draw.text((x1 + 2, y1 - text_height - 2), label, fill=(255, 255, 255), font=font)
        
        return frame


