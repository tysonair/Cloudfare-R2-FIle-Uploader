import sys
import os
import warnings
import urllib3
from PyQt6.QtWidgets import (QApplication, QMainWindow, QPushButton, QLabel, 
                            QVBoxLayout, QHBoxLayout, QWidget, QFileDialog, 
                            QTextEdit, QLineEdit, QMessageBox, QProgressBar,
                            QProgressDialog, QTreeWidget, QTreeWidgetItem, QStyle,
                            QMenu, QInputDialog, QSizePolicy, QStackedWidget, QListWidget, QListWidgetItem)
from PyQt6.QtCore import Qt, QDateTime, QThread, pyqtSignal, QSize, QObject
from PyQt6.QtGui import QKeySequence, QShortcut
import boto3
from botocore.config import Config
from dotenv import load_dotenv
from PyQt6.QtGui import QClipboard
import csv
import time
import math

# 禁用 SSL 警告
warnings.filterwarnings('ignore', category=urllib3.exceptions.InsecureRequestWarning)

class UploadThread(QThread):
    progress_updated = pyqtSignal(int)
    status_updated = pyqtSignal(str, bool)
    speed_updated = pyqtSignal(float)
    upload_finished = pyqtSignal(bool, str)

    def __init__(self, s3_client, bucket_name, local_path, r2_key):
        super().__init__()
        self.s3_client = s3_client
        self.bucket_name = bucket_name
        self.local_path = local_path
        self.r2_key = r2_key
        self.is_cancelled = False
        self.last_time = time.time()
        self.last_uploaded = 0
        self.total_size = os.path.getsize(local_path)

    def _create_callback(self):
        """创建上传进度回调"""
        def callback(bytes_amount):
            current_time = time.time()
            self.last_uploaded += bytes_amount
            
            # 更新进度
            percentage = (self.last_uploaded / self.total_size) * 100
            self.progress_updated.emit(int(percentage))
            
            # 计算并更新速度
            time_diff = current_time - self.last_time
            if time_diff >= 0.5:  # 每0.5秒更新一次速度
                speed = bytes_amount / time_diff
                self.speed_updated.emit(speed)
                self.last_time = current_time
            
            return not self.is_cancelled
            
        return callback

    def run(self):
        try:
            callback = self._create_callback()
            
            if self.total_size > 50 * 1024 * 1024:  # 大于50MB使用分片上传
                self._upload_large_file(callback)
            else:
                self.s3_client.upload_file(
                    self.local_path,
                    self.bucket_name,
                    self.r2_key,
                    Callback=callback
                )

            self.upload_finished.emit(True, f"文件上传成功：{os.path.basename(self.local_path)}")
        except Exception as e:
            self.upload_finished.emit(False, f"上传失败：{str(e)}")

    def _upload_large_file(self, progress_callback):
        chunk_size = 20 * 1024 * 1024  # 20MB
        try:
            mpu = self.s3_client.create_multipart_upload(
                Bucket=self.bucket_name,
                Key=self.r2_key
            )

            parts = []
            uploaded = 0
            
            with open(self.local_path, 'rb') as f:
                part_number = 1
                while True:
                    data = f.read(chunk_size)
                    if not data:
                        break

                    response = self.s3_client.upload_part(
                        Bucket=self.bucket_name,
                        Key=self.r2_key,
                        PartNumber=part_number,
                        UploadId=mpu['UploadId'],
                        Body=data
                    )

                    parts.append({
                        'PartNumber': part_number,
                        'ETag': response['ETag']
                    })

                    uploaded += len(data)
                    progress_callback(len(data))
                    part_number += 1

            self.s3_client.complete_multipart_upload(
                Bucket=self.bucket_name,
                Key=self.r2_key,
                UploadId=mpu['UploadId'],
                MultipartUpload={'Parts': parts}
            )

        except Exception as e:
            try:
                self.s3_client.abort_multipart_upload(
                    Bucket=self.bucket_name,
                    Key=self.r2_key,
                    UploadId=mpu['UploadId']
                )
            except:
                pass
            raise e

class UploadProgressCallback:
    def __init__(self, total_size, progress_callback, status_callback, speed_callback):
        self.total_size = total_size
        self.uploaded = 0
        self.last_time = time.time()
        self.last_uploaded = 0
        self.progress_callback = progress_callback
        self.status_callback = status_callback
        self.speed_callback = speed_callback
        self.update_interval = 0.1  # 更新间隔（秒）

    def __call__(self, bytes_amount):
        self.uploaded += bytes_amount
        current_time = time.time()
        time_diff = current_time - self.last_time

        # 控制更新频率
        if time_diff >= self.update_interval:
            percentage = (self.uploaded / self.total_size) * 100
            self.progress_callback(int(percentage))

            # 计算速度
            speed = (self.uploaded - self.last_uploaded) / time_diff
            self.speed_callback(speed)

            # 只在100%时发送状态更新
            if percentage >= 100:
                self.status_callback(f"上传完成 - {percentage:.1f}%", False)

            self.last_time = current_time
            self.last_uploaded = self.uploaded

        return True

class UploadWorker:
    def __init__(self, parent):
        self.parent = parent
        self.last_time = time.time()
        self.last_uploaded = 0

    def __call__(self, bytes_amount):
        current_time = time.time()
        self.last_uploaded += bytes_amount
        
        # 更新进度
        if hasattr(self.parent, 'progress_bar'):
            percentage = (self.last_uploaded / self.total_size) * 100 if hasattr(self, 'total_size') else 0
            self.parent.progress_bar.setValue(int(percentage))
        
        # 计算并更新速度
        time_diff = current_time - self.last_time
        if time_diff >= 0.5:  # 每0.5秒更新一次速度
            speed = bytes_amount / time_diff
            if hasattr(self.parent, 'update_upload_info'):
                self.parent.update_upload_info(
                    os.path.dirname(self.file_path) if hasattr(self, 'file_path') else '',
                    self.total_files if hasattr(self, 'total_files') else 1,
                    self.uploaded_files if hasattr(self, 'uploaded_files') else 0,
                    os.path.basename(self.file_path) if hasattr(self, 'file_path') else '',
                    self.total_size if hasattr(self, 'total_size') else 0,
                    speed
                )
            self.last_time = current_time
        
        return True

    def set_file_info(self, file_path, total_size, part_number=None, total_parts=None):
        self.file_path = file_path
        self.total_size = total_size
        self.part_number = part_number
        self.total_parts = total_parts
        self.last_uploaded = 0
        self.last_time = time.time()

class R2UploaderGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        # 初始化线程相关的属性
        self.bucket_size_thread = None
        self.bucket_size_worker = None
        
        # 然后再初始化其他内容
        self.init_r2_client()
        self.init_ui()
        # 设置更大的默认窗口尺寸
        self.setGeometry(100, 100, 1330, 800)

    def init_r2_client(self):
        """初始化 R2 客户端"""
        load_dotenv()  # 加载 .env 文件
        
        self.account_id = os.getenv('R2_ACCOUNT_ID')
        self.access_key_id = os.getenv('R2_ACCESS_KEY_ID')
        self.access_key_secret = os.getenv('R2_ACCESS_KEY_SECRET')
        self.bucket_name = os.getenv('R2_BUCKET_NAME')
        self.endpoint_url = os.getenv('R2_ENDPOINT_URL')

        if not all([self.account_id, self.access_key_id, self.access_key_secret, 
                    self.bucket_name, self.endpoint_url]):
            QMessageBox.warning(self, '配置错误', '请确保已正确配置 R2 凭证！')
            return

        self.s3_client = boto3.client(
            service_name='s3',
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.access_key_secret,
            config=Config(
                signature_version='s3v4',
                retries={'max_attempts': 3},
            ),
            region_name='auto',
            verify=False
        )

    def init_ui(self):
        """初始化用户界面"""
        self.setWindowTitle('R2 文件上传工具')
        self.setGeometry(100, 100, 1000, 600)  # 加宽窗口

        # 创建主窗口部件和布局
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout()  # 改用水平布局作为主布局
        main_widget.setLayout(main_layout)

        # 左侧面板
        left_panel = QWidget()
        left_layout = QVBoxLayout()
        left_panel.setLayout(left_layout)

        # 添加文件选择相关控件到左侧面板
        self.file_path_input = QLineEdit()
        self.file_path_input.setPlaceholderText('选择文件或文件夹路径')
        self.file_path_input.setMinimumHeight(40)  # 增加输入框高度
        left_layout.addWidget(self.file_path_input)

        button_layout = QHBoxLayout()
        browse_file_btn = QPushButton('选择文件')
        browse_folder_btn = QPushButton('选择文件夹')
        browse_file_btn.setMinimumHeight(40)  # 增加按钮高度
        browse_folder_btn.setMinimumHeight(40)  # 增加按钮高度
        browse_file_btn.clicked.connect(self.browse_file)
        browse_folder_btn.clicked.connect(self.browse_folder)
        button_layout.addWidget(browse_file_btn)
        button_layout.addWidget(browse_folder_btn)
        left_layout.addLayout(button_layout)

        self.custom_name_input = QLineEdit()
        self.custom_name_input.setPlaceholderText('自定义文件名（可选）')
        self.custom_name_input.setMinimumHeight(40)  # 增加输入框高度
        left_layout.addWidget(self.custom_name_input)

        upload_btn = QPushButton('上传')
        upload_btn.setMinimumHeight(40)  # 增加按钮高度
        upload_btn.clicked.connect(self.upload_file)
        left_layout.addWidget(upload_btn)

        # 增加各控件之间的间距
        left_layout.setSpacing(10)  # 设置布局中控件之间的垂直间距

        self.progress_bar = QProgressBar()
        left_layout.addWidget(self.progress_bar)

        # 添加文件信息显示
        self.current_file_info = QTextEdit()
        self.current_file_info.setReadOnly(True)
        self.current_file_info.setPlaceholderText('当前文件信息')
        left_layout.addWidget(self.current_file_info)

        # 添加上传结果显示
        self.result_info = QTextEdit()
        self.result_info.setReadOnly(True)
        self.result_info.setPlaceholderText('上传结果')
        left_layout.addWidget(self.result_info)

        # 右侧面板
        right_panel = QWidget()
        right_layout = QVBoxLayout()
        right_panel.setLayout(right_layout)

        # 添加当前路径显示
        path_layout = QHBoxLayout()
        self.back_button = QPushButton('返回上级')
        self.back_button.clicked.connect(self.go_back)
        self.back_button.setEnabled(False)  # 初始禁用
        
        # 设置返回按钮的固定宽度
        self.back_button.setFixedWidth(80)  # 设置固定宽度为80像素
        # 或者设置最大宽度
        # self.back_button.setMaximumWidth(80)
        
        self.current_path_label = QLabel('当前路径: /')
        path_layout.addWidget(self.back_button)
        path_layout.addWidget(self.current_path_label)
        
        # 修改视图布局，添加之前缺失的按钮
        view_layout = QHBoxLayout()
        self.list_view_button = QPushButton('列表视图')
        self.icon_view_button = QPushButton('图标视图') 
        self.export_urls_button = QPushButton('导出所有文件URL')
        self.bucket_size_label = QLabel('桶大小: 统计中...')
        
        self.list_view_button.clicked.connect(lambda: self.switch_view('list'))
        self.icon_view_button.clicked.connect(lambda: self.switch_view('icon'))
        self.export_urls_button.clicked.connect(self.export_custom_urls)
        
        view_layout.addWidget(self.list_view_button)
        view_layout.addWidget(self.icon_view_button)
        view_layout.addWidget(self.export_urls_button)
        view_layout.addWidget(self.bucket_size_label)
        view_layout.addStretch()
        
        # 将视图布局添加到右侧布局中
        right_layout.addLayout(view_layout)
        right_layout.addLayout(path_layout)

        # 修改文件列表组件,添加图标视图
        self.stack_widget = QStackedWidget()
        
        # 表视图
        self.file_list = QTreeWidget()
        self.file_list.setHeaderLabels(['名称', '类型', '大小', '修改时间'])
        self.file_list.setColumnWidth(0, 300)
        self.file_list.itemDoubleClicked.connect(self.on_item_double_clicked)
        
        # 图标视图 
        self.icon_list = QListWidget()
        self.icon_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.icon_list.setIconSize(QSize(96, 96))
        self.icon_list.setSpacing(40)
        self.icon_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.icon_list.setMovement(QListWidget.Movement.Static)
        self.icon_list.setGridSize(QSize(200, 160))
        self.icon_list.setWordWrap(True)
        self.icon_list.setUniformItemSizes(True)
        self.icon_list.itemDoubleClicked.connect(self.on_icon_double_clicked)
        
        self.stack_widget.addWidget(self.file_list)
        self.stack_widget.addWidget(self.icon_list)
        right_layout.addWidget(self.stack_widget)

        # 添加左右面板到主布局
        main_layout.addWidget(left_panel, 1)  # 1是拉伸因子
        main_layout.addWidget(right_panel, 1)

        # 初始化当前路径
        self.current_path = ''

        # 初始化显示文件列表，但不计算桶大小
        self.refresh_file_list(calculate_bucket_size=True)  # 仅在初始化时计算一次桶大小

        # 为文件列表右键菜单
        self.file_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self.show_context_menu)

        # 为图标视图添加右键菜单
        self.icon_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.icon_list.customContextMenuRequested.connect(self.show_icon_context_menu)

        # 添加快捷键支持
        self.file_list.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.icon_list.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # 在 init_ui 方法末尾添加快捷键设置
        # 删除文件快捷键 (Ctrl+D)
        delete_shortcut = QShortcut(QKeySequence("Ctrl+D"), self)
        delete_shortcut.activated.connect(self.delete_selected_item)

        # 删除目录快捷键 (Ctrl+L)
        delete_dir_shortcut = QShortcut(QKeySequence("Ctrl+L"), self)
        delete_dir_shortcut.activated.connect(self.delete_selected_directory)

        # 进入目录快捷键 (Enter)
        enter_dir_shortcut = QShortcut(QKeySequence("Return"), self)
        enter_dir_shortcut.activated.connect(self.enter_selected_directory)

        # 自定义域名分享快捷键 (Ctrl+Z)
        custom_share_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self)
        custom_share_shortcut.activated.connect(lambda: self.share_selected_item(True))

        # R2.dev分享快捷键 (Ctrl+E)
        r2_share_shortcut = QShortcut(QKeySequence("Ctrl+E"), self)
        r2_share_shortcut.activated.connect(lambda: self.share_selected_item(False))

    def browse_file(self):
        """打开文件选择对话框"""
        file_name, _ = QFileDialog.getOpenFileName(self, '选择文件')
        if file_name:
            self.file_path_input.setText(file_name)

    def browse_folder(self):
        """打开文件夹选择对话框"""
        folder_path = QFileDialog.getExistingDirectory(self, '选文件夹')
        if folder_path:
            self.file_path_input.setText(folder_path)
            # 显示待上传文件列表
            self.show_pending_files(folder_path)

    def show_pending_files(self, folder_path):
        """显示待上传的文列表"""
        try:
            total_size = 0
            file_list = []
            
            # 遍历文件夹获取所有文件信息
            for root, _, files in os.walk(folder_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    relative_path = os.path.relpath(file_path, folder_path)
                    size = os.path.getsize(file_path)
                    total_size += size
                    file_list.append((relative_path, size))

            # 格式化显示信息
            info_text = f"文件夹路径：{folder_path}\n"
            info_text += f"总文件数：{len(file_list)} 个\n"
            info_text += f"总大小：{total_size / 1024 / 1024:.2f} MB\n\n"
            info_text += "待上传文件列表：\n"
            info_text += "-" * 50 + "\n"
            
            # 添加文件列表，按照文件大小降序排序
            for relative_path, size in sorted(file_list, key=lambda x: x[1], reverse=True):
                info_text += f"📄 {relative_path}\n"
                info_text += f"   大小：{size / 1024 / 1024:.2f} MB\n"
            
            self.current_file_info.setText(info_text)

        except Exception as e:
            self.current_file_info.setText(f"获取文列表失败：{str(e)}")

    def _upload_single_file(self, file_path):
        """上传单文件，支持分片上传"""
        try:
            file_size = os.path.getsize(file_path)
            file_info = f"文件路径：{file_path}\n"
            file_info += f"文件大小：{file_size / 1024 / 1024:.2f} MB\n"
            file_info += f"文件类型：{os.path.splitext(file_path)[1]}"
            self.current_file_info.setText(file_info)

            custom_name = self.custom_name_input.text()
            r2_key = custom_name if custom_name else os.path.basename(file_path)

            # 显示开始上传的消息
            self.show_result(f'开始上传文件: {r2_key}', False)

            # 设置分片大小为20MB
            chunk_size = 20 * 1024 * 1024  # 20MB in bytes
            
            # 如果文件大小超过50MB，使用分片上传
            if file_size > 50 * 1024 * 1024:  # 50MB
                try:
                    # 初始化分片上传
                    mpu = self.s3_client.create_multipart_upload(
                        Bucket=self.bucket_name,
                        Key=r2_key
                    )
                    
                    # 计算分片数量
                    total_parts = (file_size + chunk_size - 1) // chunk_size
                    parts = []
                    total_uploaded = 0
                    
                    with open(file_path, 'rb') as f:
                        for part_number in range(1, total_parts + 1):
                            # 读取分片数据
                            data = f.read(chunk_size)
                            data_len = len(data)
                            total_uploaded += data_len
                            
                            # 创建进度回调
                            self.upload_worker = UploadWorker(self)
                            self.upload_worker.progress_updated.connect(self.progress_bar.setValue)
                            self.upload_worker.status_updated.connect(self.show_result)
                            self.upload_worker.set_file_info(
                                file_path, 
                                file_size,  # 使用总文件大小而不是分片大小
                                part_number, 
                                total_parts
                            )
                            
                            # 更新总体进度
                            percentage = (total_uploaded / file_size) * 100
                            self.progress_bar.setValue(int(percentage))
                            self.show_result(
                                f'正在上传: {os.path.basename(file_path)} - {percentage:.1f}% (分片 {part_number}/{total_parts})', 
                                False
                            )
                            
                            # 上传分片
                            response = self.s3_client.upload_part(
                                Bucket=self.bucket_name,
                                Key=r2_key,
                                PartNumber=part_number,
                                UploadId=mpu['UploadId'],
                                Body=data
                            )
                            
                            # 记录分片信息
                            parts.append({
                                'PartNumber': part_number,
                                'ETag': response['ETag']
                            })
                            
                            self.show_result(f'分片 {part_number}/{total_parts} 上传完成', False)
                    
                    # 完成分片上传
                    self.s3_client.complete_multipart_upload(
                        Bucket=self.bucket_name,
                        Key=r2_key,
                        UploadId=mpu['UploadId'],
                        MultipartUpload={'Parts': parts}
                    )
                    
                except Exception as e:
                    # 如果上传失败，中止分片上传
                    self.s3_client.abort_multipart_upload(
                        Bucket=self.bucket_name,
                        Key=r2_key,
                        UploadId=mpu['UploadId']
                    )
                    raise e
                
            else:
                # 小文件使用普通上传
                self.upload_worker = UploadWorker(self)
                self.upload_worker.progress_updated.connect(self.progress_bar.setValue)
                self.upload_worker.status_updated.connect(self.show_result)
                self.upload_worker.set_file_info(file_path, file_size)

                self.s3_client.upload_file(
                    file_path, 
                    self.bucket_name, 
                    r2_key,
                    Callback=self.upload_worker
                )

            self.progress_bar.setValue(100)
            self.show_result(f'文件 {r2_key} 上传成功！', False)

        except Exception as e:
            self.show_result(f'上传失败：{str(e)}', True)
        finally:
            self.progress_bar.setValue(0)
            self.file_path_input.clear()
            self.custom_name_input.clear()

    def _upload_folder(self, folder_path):
        """上传文件夹"""
        try:
            self.current_upload_folder = folder_path
            base_folder_name = os.path.basename(folder_path)
            all_files = self._get_folder_files(folder_path)
            
            total_files = len(all_files)
            if total_files == 0:
                self.show_result('文件夹为空，没有上传的文件', True)
                return

            self.show_result(f'开始上传文件夹: {folder_path}', False)
            uploaded_files = 0
            failed_files = []

            self.update_upload_info(self.current_upload_folder, total_files, uploaded_files)

            for local_path, relative_path in all_files:
                try:
                    r2_key = os.path.join(base_folder_name, relative_path).replace('\\', '/')
                    file_size = os.path.getsize(local_path)
                    current_file = os.path.basename(local_path)

                    # 显示开始上传当前文件的信息
                    self.show_result(f'开始上传: {current_file} ({self._format_size(file_size)})', False)

                    # 更新显示信息（不包含速度）
                    self.update_upload_info(
                        self.current_upload_folder, 
                        total_files, 
                        uploaded_files,
                        current_file,
                        file_size
                    )

                    # 创建并启动上传线程
                    upload_thread = UploadThread(
                        self.s3_client,
                        self.bucket_name,
                        local_path,
                        r2_key
                    )

                    # 使用 lambda 捕获当前文件信息
                    current_file_info = {
                        'name': current_file,
                        'size': file_size
                    }
                    
                    # 连接信号
                    upload_thread.progress_updated.connect(self.progress_bar.setValue)
                    upload_thread.status_updated.connect(self.show_result)
                    upload_thread.speed_updated.connect(
                        lambda speed: self.update_upload_info(
                            self.current_upload_folder,
                            total_files,
                            uploaded_files,
                            current_file_info['name'],
                            current_file_info['size'],
                            speed
                        )
                    )

                    # 启动线程并等待完成
                    upload_thread.start()
                    while not upload_thread.isFinished():
                        QApplication.processEvents()
                        time.sleep(0.1)

                    if upload_thread.isFinished():
                        uploaded_files += 1
                        self.show_result(f'✅ 文件上传成功: {current_file}', False)
                        self.update_upload_info(self.current_upload_folder, total_files, uploaded_files)

                except Exception as e:
                    error_msg = f'❌ 文件上传失败：{os.path.basename(local_path)} - {str(e)}'
                    self.show_result(error_msg, True)
                    failed_files.append((relative_path, str(e)))

                finally:
                    self.progress_bar.setValue(0)
                    QApplication.processEvents()

            # 显示最终上传结果
            self._show_final_results(uploaded_files, total_files, failed_files)

        except Exception as e:
            self.show_result(f'文件夹上���失败：{str(e)}', True)
        finally:
            self.progress_bar.setValue(0)

    def calculate_bucket_size(self):
        """计算整个桶的总大小"""
        try:
            # 更新标签显示正在统计
            self.bucket_size_label.setText('桶大小: 统计中...')
            QApplication.processEvents()  # 确保UI更新
            
            total_size = 0
            paginator = self.s3_client.get_paginator('list_objects_v2')
            
            # 遍历所有对象，不使用 prefix
            for page in paginator.paginate(Bucket=self.bucket_name):
                if 'Contents' in page:
                    for obj in page['Contents']:
                        if not obj['Key'].endswith('/'):  # 排除目录
                            total_size += obj['Size']
            
            # 更新显示
            formatted_size = self._format_size(total_size)
            self.bucket_size_label.setText(f'桶大小: {formatted_size}')
            
        except Exception as e:
            print(f"计算桶大小时发生错误: {str(e)}")
            self.bucket_size_label.setText('桶大小: 计算失败')

    def refresh_file_list(self, prefix='', calculate_bucket_size=False):
        """刷新文件列表"""
        try:
            # 清空当前显示
            self.file_list.clear()
            self.icon_list.clear()
            
            # 仅在需要时计算桶大小
            if calculate_bucket_size:
                self.calculate_bucket_size()
                
            # 获取文件列表
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name, 
                Prefix=prefix, 
                Delimiter='/'
            )
            
            # 更新当前路径显示
            self.current_path_label.setText(f'当前路径: /{prefix}')
            self.current_path = prefix
            self.back_button.setEnabled(bool(prefix))
            
            # 存储文件和目录项，以便排序
            files = []
            directories = []
            
            # 处理文件
            if 'Contents' in response:
                for obj in response['Contents']:
                    if obj['Key'] == prefix or obj['Key'].endswith('/'):
                        continue
                    
                    file_name = obj['Key'].split('/')[-1]
                    files.append({
                        'name': file_name,
                        'key': obj['Key'],
                        'size': obj['Size'],
                        'last_modified': obj['LastModified']
                    })
            
            # 处理目录
            if 'CommonPrefixes' in response:
                for prefix_obj in response['CommonPrefixes']:
                    dir_name = prefix_obj['Prefix'].rstrip('/').split('/')[-1] + '/'
                    directories.append({
                        'name': dir_name,
                        'prefix': prefix_obj['Prefix']
                    })
            
            # 按最后修改时间降序排文件（最新的在前面）
            files.sort(key=lambda x: x['last_modified'], reverse=True)
            
            # 先添加文件
            for file in files:
                # 列表视图项
                tree_item = QTreeWidgetItem(self.file_list)
                tree_item.setText(0, file['name'])
                tree_item.setText(1, self._get_file_type(file['name']))
                tree_item.setText(2, self._format_size(file['size']))
                tree_item.setText(3, file['last_modified'].strftime('%Y-%m-%d %H:%M:%S'))
                tree_item.setIcon(0, self._get_file_icon(file['name']))
                tree_item.setData(0, Qt.ItemDataRole.UserRole, file['key'])
                
                # 标图项
                icon_item = QListWidgetItem(self.icon_list)
                icon_item.setText(file['name'])
                icon_item.setIcon(self._get_file_icon(file['name']))
                icon_item.setData(Qt.ItemDataRole.UserRole, file['key'])
                icon_item.setData(Qt.ItemDataRole.UserRole + 1, 'file')
            
            # 再添加目录
            for directory in directories:
                # 列表视图项
                tree_item = QTreeWidgetItem(self.file_list)
                tree_item.setText(0, directory['name'])
                tree_item.setText(1, '目录')
                tree_item.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
                tree_item.setData(0, Qt.ItemDataRole.UserRole, directory['prefix'])
                
                # 图标视图项
                icon_item = QListWidgetItem(self.icon_list)
                icon_item.setText(directory['name'])
                icon_item.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
                icon_item.setData(Qt.ItemDataRole.UserRole, directory['prefix'])
                icon_item.setData(Qt.ItemDataRole.UserRole + 1, 'directory')

        except Exception as e:
            QMessageBox.warning(self, '错误', f'获取文件列表失败：{str(e)}')

    def on_item_double_clicked(self, item):
        """处理双击事件"""
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if item.text(1) == '目录':
            self.refresh_file_list(path, calculate_bucket_size=False)  # 不重新计算桶大小

    def go_back(self):
        """返回上级目录"""
        if self.current_path:
            # 除去最后一个目录
            parent_path = '/'.join(self.current_path.rstrip('/').split('/')[:-1])
            if parent_path:
                parent_path += '/'
            self.refresh_file_list(parent_path, calculate_bucket_size=False)  # 不重新计算桶大小

    def _get_file_type(self, filename):
        """获取文件型"""
        ext = os.path.splitext(filename)[1].lower()
        if not ext:
            return '--'
        return ext[1:].upper()  # 移除点号并转为大写

    def _format_size(self, size_in_bytes):
        """格式化文件大小"""
        try:
            # 定义单位和转换基数
            units = ['B', 'KB', 'MB', 'GB', 'TB']
            base = 1024
            
            # 如果小于1024字节，直接返回字节大小
            if size_in_bytes < base:
                return f"{size_in_bytes:.2f} B"
            
            # 计算合适的单位级别
            exp = int(math.log(size_in_bytes, base))
            if exp >= len(units):
                exp = len(units) - 1
                
            # 计算最终大小
            final_size = size_in_bytes / (base ** exp)
            return f"{final_size:.2f} {units[exp]}"
            
        except Exception as e:
            return "计算错误"

    def show_result(self, message, is_error=False):
        """示执行结果（倒序显示，最新的在上面）"""
        timestamp = QDateTime.currentDateTime().toString('yyyy-MM-dd hh:mm:ss')
        formatted_message = f"[{timestamp}] {'❌ ' if is_error else '✅ '}{message}"
        
        # 获取当前的文本内
        current_text = self.result_info.toPlainText()
        
        # 将新消息添加到最前面
        if current_text:
            new_text = formatted_message + '\n' + current_text
        else:
            new_text = formatted_message
        
        # 更新文本显示
        self.result_info.setText(new_text)
        
        # 将滚动条移动到顶部
        self.result_info.verticalScrollBar().setValue(0)

    def get_public_url(self, object_key):
        """生成永久公开访问链接"""
        # 使用自定义域名
        custom_domain = "r2.lss.lol"
        
        # 确保 object_key 开头没有斜杠
        object_key = object_key.lstrip('/')
        
        # 直接返回完整 URL，不包含 bucket_name
        return f"https://{custom_domain}/{object_key}"

    def generate_presigned_url(self, object_key, expiration=3600):
        """生成临时访问链接
        object_key: 文件的键名
        expiration: 链接有效期(秒)，默认1小时
        """
        try:
            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket_name,
                    'Key': object_key
                },
                ExpiresIn=expiration
            )
            return url
        except Exception as e:
            print(f"生成访问链接失败：{str(e)}")
            return None

    def show_context_menu(self, position):
        """显示右键菜单"""
        item = self.file_list.itemAt(position)
        if item is None:
            return

        menu = QMenu()
        
        if item.text(1) == '目录':
            # 目录操作菜单
            enter_dir = menu.addAction("进入目录 (Enter)")
            enter_dir.triggered.connect(lambda: self.on_item_double_clicked(item))
            
            delete_dir = menu.addAction("删除目录 (Ctrl+L)")
            delete_dir.triggered.connect(lambda: self.delete_directory(item.data(0, Qt.ItemDataRole.UserRole)))
        else:
            # 文件操作菜单
            delete_action = menu.addAction("删除文件 (Ctrl+D)")
            delete_action.triggered.connect(lambda: self.delete_file(item))
            
            custom_domain = menu.addAction("通过自定义域名分享 (Ctrl+Z)")
            r2_domain = menu.addAction("通过 R2.dev 分享 (Ctrl+E)")
            
            custom_domain.triggered.connect(
                lambda: self.generate_public_share(item, use_custom_domain=True)
            )
            r2_domain.triggered.connect(
                lambda: self.generate_public_share(item, use_custom_domain=False)
            )

        menu.exec(self.file_list.viewport().mapToGlobal(position))

    def delete_file(self, item):
        """删除文件"""
        object_key = item.data(0, Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(
            self, 
            '确认删除', 
            f'确定要删除文件 {item.text(0)} 吗？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.s3_client.delete_object(
                    Bucket=self.bucket_name,
                    Key=object_key
                )
                self.show_result(f'文件 {item.text(0)} 已删除', False)
                # 刷新文件列表并更新桶大小
                self.refresh_file_list(self.current_path, calculate_bucket_size=True)
            except Exception as e:
                self.show_result(f'删除文件失败：{str(e)}', True)

    def generate_public_share(self, item, use_custom_domain=True):
        """生成永久分享链接"""
        object_key = item.data(0, Qt.ItemDataRole.UserRole)
        
        if use_custom_domain:
            domain = os.getenv('R2_CUSTOM_DOMAIN')
            domain_type = "自定义域名"
            url = f"https://{domain}/{object_key}"
        else:
            domain = os.getenv('R2_PUBLIC_DOMAIN')
            domain_type = "R2.dev"
            url = f"https://{domain}/{object_key}"
        
        # 复制到剪贴板
        clipboard = QApplication.clipboard()
        clipboard.setText(url)
        self.show_result(f"已复制{domain_type}访问链接到剪贴板: {url}", False)

    def switch_view(self, view_type):
        """切换表/图标视图"""
        if view_type == 'list':
            self.stack_widget.setCurrentIndex(0)
        else:
            self.stack_widget.setCurrentIndex(1)
        self.refresh_file_list(self.current_path)

    def on_icon_double_clicked(self, item):
        """处理图标视图的双击事件"""
        path = item.data(Qt.ItemDataRole.UserRole)
        if item.data(Qt.ItemDataRole.UserRole + 1) == 'directory':
            self.refresh_file_list(path)

    def show_icon_context_menu(self, position):
        """显示图标视图的右键菜单"""
        item = self.icon_list.itemAt(position)
        if item is None:
            return

        menu = QMenu()
        
        if item.data(Qt.ItemDataRole.UserRole + 1) == 'directory':
            # 目录操作菜单
            enter_dir = menu.addAction("进入目录 (Enter)")
            enter_dir.triggered.connect(lambda: self.on_icon_double_clicked(item))
            
            delete_dir = menu.addAction("删除目录 (Ctrl+L)")
            delete_dir.triggered.connect(
                lambda: self.delete_directory(item.data(Qt.ItemDataRole.UserRole))
            )
        else:
            # 文件操作菜单
            delete_action = menu.addAction("删除文件 (Ctrl+D)")
            delete_action.triggered.connect(lambda: self.delete_icon_file(item))
            
            custom_domain = menu.addAction("通过自定义域名分享 (Ctrl+Z)")
            r2_domain = menu.addAction("通过 R2.dev 分享 (Ctrl+E)")
            
            custom_domain.triggered.connect(
                lambda: self.generate_public_share_icon(item, use_custom_domain=True)
            )
            r2_domain.triggered.connect(
                lambda: self.generate_public_share_icon(item, use_custom_domain=False)
            )

        menu.exec(self.icon_list.viewport().mapToGlobal(position))

    def delete_icon_file(self, item):
        """删除图标视图中的文件"""
        object_key = item.data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(
            self, 
            '确认删除', 
            f'确定要删除文件 {item.text()} 吗？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.s3_client.delete_object(
                    Bucket=self.bucket_name,
                    Key=object_key
                )
                self.show_result(f'文件 {item.text()} 已删除', False)
                # 刷新文件列表并更新桶大小
                self.refresh_file_list(self.current_path, calculate_bucket_size=True)
            except Exception as e:
                self.show_result(f'删除文件失败：{str(e)}', True)

    def generate_public_share_icon(self, item, use_custom_domain=True):
        """为图标视图生成永久分享链接"""
        object_key = item.data(Qt.ItemDataRole.UserRole)
        
        if use_custom_domain:
            domain = os.getenv('R2_CUSTOM_DOMAIN')
            domain_type = "自定义域名"
            url = f"https://{domain}/{object_key}"
        else:
            domain = os.getenv('R2_PUBLIC_DOMAIN')
            domain_type = "R2.dev"
            url = f"https://{domain}/{object_key}"
        
        # 复制到剪贴板
        clipboard = QApplication.clipboard()
        clipboard.setText(url)
        self.show_result(f"复制{domain_type}访问链接到剪贴板: {url}", False)

    def delete_selected_item(self):
        """处理删除快捷键"""
        if self.stack_widget.currentIndex() == 0:  # 列表视图
            item = self.file_list.currentItem()
            if item and item.text(1) != '目录':
                self.delete_file(item)
        else:  # 图标视图
            item = self.icon_list.currentItem()
            if item and item.data(Qt.ItemDataRole.UserRole + 1) != 'directory':
                self.delete_icon_file(item)

    def share_selected_item(self, use_custom_domain):
        """处理分享快捷键"""
        if self.stack_widget.currentIndex() == 0:  # 列表视图
            item = self.file_list.currentItem()
            if item and item.text(1) != '目录':
                self.generate_public_share(item, use_custom_domain)
        else:  # 图标视图
            item = self.icon_list.currentItem()
            if item and item.data(Qt.ItemDataRole.UserRole + 1) != 'directory':
                self.generate_public_share_icon(item, use_custom_domain)

    def _get_file_icon(self, filename):
        """据文件类型回对应的图标"""
        ext = os.path.splitext(filename)[1].lower()
        
        # 定义文件类型和对应标
        icon_map = {
            # 图片文件
            '.jpg': QStyle.StandardPixmap.SP_FileDialogDetailedView,
            '.jpeg': QStyle.StandardPixmap.SP_FileDialogDetailedView,
            '.png': QStyle.StandardPixmap.SP_FileDialogDetailedView,
            '.gif': QStyle.StandardPixmap.SP_FileDialogDetailedView,
            '.bmp': QStyle.StandardPixmap.SP_FileDialogDetailedView,
            
            # 文档文件
            '.pdf': QStyle.StandardPixmap.SP_FileDialogInfoView,
            '.doc': QStyle.StandardPixmap.SP_FileDialogInfoView,
            '.docx': QStyle.StandardPixmap.SP_FileDialogInfoView,
            '.txt': QStyle.StandardPixmap.SP_FileDialogInfoView,
            
            # 压缩文件
            '.zip': QStyle.StandardPixmap.SP_DriveFDIcon,
            '.rar': QStyle.StandardPixmap.SP_DriveFDIcon,
            '.7z': QStyle.StandardPixmap.SP_DriveFDIcon,
            
            # 音视频文件
            '.mp3': QStyle.StandardPixmap.SP_MediaVolume,
            '.wav': QStyle.StandardPixmap.SP_MediaVolume,
            '.mp4': QStyle.StandardPixmap.SP_MediaPlay,
            '.avi': QStyle.StandardPixmap.SP_MediaPlay,
            '.mov': QStyle.StandardPixmap.SP_MediaPlay,
            
            # 代码文件
            '.py': QStyle.StandardPixmap.SP_FileDialogContentsView,
            '.js': QStyle.StandardPixmap.SP_FileDialogContentsView,
            '.html': QStyle.StandardPixmap.SP_FileDialogContentsView,
            '.css': QStyle.StandardPixmap.SP_FileDialogContentsView,
        }
        
        # 返回对应的图标,如果没有匹配则返回默认文件标
        return self.style().standardIcon(icon_map.get(ext, QStyle.StandardPixmap.SP_FileIcon))

    def export_custom_urls(self):
        """导出所有文件的自定义域名URL和文件大小"""
        try:
            # 显示开始信息
            self.show_result("开始导出文件URL列表...", False)
            
            # 获取所有文件列表
            all_files = []
            paginator = self.s3_client.get_paginator('list_objects_v2')
            
            # 更新标签显示正在统计
            self.show_result("正在遍历所有文件...", False)
            QApplication.processEvents()
            
            # 遍历所有对象
            for page in paginator.paginate(Bucket=self.bucket_name):
                if 'Contents' in page:
                    for obj in page['Contents']:
                        if not obj['Key'].endswith('/'):  # 排除目录
                            all_files.append({
                                'key': obj['Key'],
                                'size': obj['Size']  # 添加文件大小
                            })

            # 计算总文件数
            total_files = len(all_files)
            if total_files == 0:
                self.show_result("没有找到可导出的文件", False)
                return

            self.show_result(f"找到 {total_files} 文件，开始生成URL...", False)
            
            # 获取当前时间并格式化
            current_time = QDateTime.currentDateTime().toString('yyyyMMdd_HHmmss')
            
            # 获取脚本所在目录的绝对路径，并生成带时间戳的文件名
            script_dir = os.path.dirname(os.path.abspath(__file__))
            csv_path = os.path.join(script_dir, f'file_customUrl_{current_time}.csv')
            
            self.show_result(f"备导出到文件: {csv_path}", False)
            
            # 写入CSV文件，使用 utf-8-sig 编码（带BOM）
            with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['文件名', '文件路径', 'URL', '文件大小'])  # 添加文件大小列
                
                # 显示写入表头信息
                self.show_result("已创建CSV文件并写入表头", False)
                
                processed_count = 0
                for i, file_info in enumerate(all_files, 1):
                    # 生成自定义域名URL
                    custom_url = f"https://r2.lss.lol/{file_info['key']}"
                    
                    # 获取文件名
                    file_name = os.path.basename(file_info['key'])
                    
                    # 格式化文件大小
                    formatted_size = self._format_size(file_info['size'])
                    
                    # 写入数据
                    writer.writerow([
                        file_name, 
                        file_info['key'], 
                        custom_url,
                        formatted_size  # 添加格式化后的文件大小
                    ])
                    
                    processed_count = i
                    
                    # 每处理50个文件更新一次显示信息
                    if i % 50 == 0 or i == total_files:
                        self.show_result(f"已处理: {i}/{total_files} 个文件", False)
                        QApplication.processEvents()
            
            # 显示完成信息
            final_message = (
                f"导出完成！\n"
                f"- 总文件数: {total_files}\n"
                f"- 已处理: {processed_count}\n"
                f"- 导出文件: {csv_path}"
            )
            self.show_result(final_message, False)

        except Exception as e:
            error_message = f"导出失败：{str(e)}"
            self.show_result(error_message, True)

    def update_upload_info(self, folder_path, total_files, uploaded_files, current_file=None, file_size=None, speed=None):
        """更新传信息显示"""
        info = f"文件夹路径：{folder_path}\n"
        info += f"已上传文件：{uploaded_files}/{total_files}\n\n"
        
        if current_file:
            info += "当前上传文件："
            if speed:
                info += f" (上传速度：{self._format_speed(speed)})\n"
            else:
                info += "\n"
            if file_size:
                info += f"{current_file} ({self._format_size(file_size)})"
        
        self.current_file_info.setText(info)

    def handle_status_update(self, message, is_error=False):
        """处理状态更新，只在100%时显示"""
        if "100.0%" in message:
            self.show_result(message, is_error)

    def _format_speed(self, bytes_per_second):
        """格式化速度显示"""
        if bytes_per_second < 1024:
            return f"{bytes_per_second:.1f} B/s"
        elif bytes_per_second < 1024 * 1024:
            return f"{bytes_per_second/1024:.1f} KB/s"
        else:
            return f"{bytes_per_second/1024/1024:.1f} MB/s"

    def upload_file(self):
        """处理文件上传"""
        file_path = self.file_path_input.text().strip()
        if not file_path:
            self.show_result('请选择要上传的文件或文件夹', True)
            return
        
        if not os.path.exists(file_path):
            self.show_result('选择的文件或文件夹不存在', True)
            return
        
        try:
            # 根据是文件还是文件夹选择不同的上传方
            if os.path.isfile(file_path):
                # 单个文件上传
                file_size = os.path.getsize(file_path)
                file_name = os.path.basename(file_path)
                
                # 如果有自定义文件名，使用自定义的
                custom_name = self.custom_name_input.text().strip()
                if custom_name:
                    file_name = custom_name
                
                self.show_result(f'开始上传文件: {file_name}', False)
                
                # 创建并启动上传线程
                upload_thread = UploadThread(
                    self.s3_client,
                    self.bucket_name,
                    file_path,
                    file_name
                )
                
                # 连接信号
                upload_thread.progress_updated.connect(self.progress_bar.setValue)
                upload_thread.status_updated.connect(self.show_result)
                upload_thread.speed_updated.connect(
                    lambda speed: self.update_upload_info(
                        os.path.dirname(file_path),
                        1,
                        0,
                        file_name,
                        file_size,
                        speed
                    )
                )
                upload_thread.upload_finished.connect(
                    lambda success, msg: self._handle_upload_finished(
                        success, msg, 0, 1
                    )
                )
                
                # 启动线程并等待完成
                upload_thread.start()
                while not upload_thread.isFinished():
                    QApplication.processEvents()
                    time.sleep(0.1)
                
                # 上传完成后刷新文件列表并重新计算桶大小
                self.refresh_file_list(self.current_path, calculate_bucket_size=True)
                
            else:
                # 文件夹上传
                self._upload_folder(file_path)
                # 上传完成后刷新文件表并重新计算桶大小
                self.refresh_file_list(self.current_path, calculate_bucket_size=True)
                
        except Exception as e:
            self.show_result(f'上传失败：{str(e)}', True)
        finally:
            self.progress_bar.setValue(0)
            self.file_path_input.clear()
            self.custom_name_input.clear()

    def _get_folder_files(self, folder_path):
        """获取文件夹中的所有文件列表"""
        all_files = []
        try:
            for root, _, files in os.walk(folder_path):
                for file in files:
                    local_path = os.path.join(root, file)
                    relative_path = os.path.relpath(local_path, folder_path)
                    all_files.append((local_path, relative_path))
        except Exception as e:
            self.show_result(f'获取文件列表失败：{str(e)}', True)
            return []
        
        return all_files

    def _handle_upload_finished(self, success, message, uploaded_files, total_files):
        """处理上传完成的回调"""
        if success:
            # 更新已上传文件计数
            uploaded_files += 1
            # 更新显示
            self.show_result(message, False)
            # 更新进度信息
            self.update_upload_info(
                os.path.dirname(self.file_path_input.text().strip()),
                total_files,
                uploaded_files
            )
        else:
            # 显示错误信息
            self.show_result(message, True)
        
        # 重置进度条
        self.progress_bar.setValue(0)
        QApplication.processEvents()

    def _show_final_results(self, uploaded_files, total_files, failed_files):
        """显示最终上传结果"""
        if failed_files:
            self.show_result(
                f'文件夹上传完成，但有{len(failed_files)}个文件失败。'
                f'成功：{uploaded_files}/{total_files}', True
            )
            # 显示失败文件列表
            self.show_result("失败文件列表：", True)
            for failed_file, error in failed_files:
                self.show_result(f"❌ {failed_file}: {error}", True)
        else:
            self.show_result(
                f'✅ 文件夹上传完成！成功上传 {uploaded_files}/{total_files} 个文件', 
                False
            )
        
        # 使用保存的完整文件夹路径
        self.update_upload_info(
            self.current_upload_folder,
            total_files,
            uploaded_files
        )

    def delete_directory(self, prefix):
        """删除目录及其所有内容"""
        try:
            # 获取目录下所有对象
            paginator = self.s3_client.get_paginator('list_objects_v2')
            total_objects = 0
            deleted_objects = 0
            
            # 首先计算总对象数
            for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
                if 'Contents' in page:
                    total_objects += len(page['Contents'])
            
            if total_objects == 0:
                self.show_result(f'目录 {prefix} 为空', False)
                return
            
            # 确认删除
            reply = QMessageBox.question(
                self,
                '确认删除',
                f'确定要删除目录 {prefix} 及其中的 {total_objects} 个文件吗？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                # 创建进度对话框
                progress = QProgressDialog("正在删除文件...", "取消", 0, total_objects, self)
                progress.setWindowTitle("删除进度")
                progress.setWindowModality(Qt.WindowModality.WindowModal)
                
                # 删除所有对象
                for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
                    if 'Contents' in page:
                        for obj in page['Contents']:
                            if progress.wasCanceled():
                                self.show_result(f'删除操作已取消，已删除 {deleted_objects} 个文件', True)
                                return
                                
                            self.s3_client.delete_object(
                                Bucket=self.bucket_name,
                                Key=obj['Key']
                            )
                            deleted_objects += 1
                            progress.setValue(deleted_objects)
                            
                progress.close()
                self.show_result(f'目录 {prefix} 已删除，共删除 {deleted_objects} 个文件', False)
                # 刷新文件列表并更新桶大小
                self.refresh_file_list(self.current_path, calculate_bucket_size=True)
                
        except Exception as e:
            self.show_result(f'删除目录失败：{str(e)}', True)

    # 添加新的方法来处理快捷键操作
    def enter_selected_directory(self):
        """处理进入目录的快捷键"""
        if self.stack_widget.currentIndex() == 0:  # 列表视图
            item = self.file_list.currentItem()
            if item and item.text(1) == '目录':
                self.on_item_double_clicked(item)
        else:  # 图标视图
            item = self.icon_list.currentItem()
            if item and item.data(Qt.ItemDataRole.UserRole + 1) == 'directory':
                self.on_icon_double_clicked(item)

    def delete_selected_directory(self):
        """处理删除目录的快捷键"""
        if self.stack_widget.currentIndex() == 0:  # 列表视图
            item = self.file_list.currentItem()
            if item and item.text(1) == '目录':
                self.delete_directory(item.data(0, Qt.ItemDataRole.UserRole))
        else:  # 图标视图
            item = self.icon_list.currentItem()
            if item and item.data(Qt.ItemDataRole.UserRole + 1) == 'directory':
                self.delete_directory(item.data(Qt.ItemDataRole.UserRole))

# 添加一个新的 Worker 类来理后台计算
class Worker(QObject):
    finished = pyqtSignal()
    size_calculated = pyqtSignal(int)

    def __init__(self, s3_client, bucket_name):
        super().__init__()
        self.s3_client = s3_client
        self.bucket_name = bucket_name

    def calculate_bucket_size(self):
        """计算桶的总大小"""
        try:
            total_size = 0
            paginator = self.s3_client.get_paginator('list_objects_v2')
            
            # 遍历所有对象
            for page in paginator.paginate(Bucket=self.bucket_name):
                if 'Contents' in page:
                    for obj in page['Contents']:
                        if not obj['Key'].endswith('/'):  # 排除目录
                            file_size = obj['Size']
                            total_size += file_size
                            print(f"添加文件: {obj['Key']}, 大小: {file_size} bytes")  # 调试信息
            
            print(f"最终计算的总大小: {total_size} bytes")  # 调试信息
            self.size_calculated.emit(total_size)
            
        except Exception as e:
            print(f"计算桶大小时发生错误: {str(e)}")  # 添加错误日志
            self.size_calculated.emit(0)  # 发送0表示计算失败
        finally:
            self.finished.emit()

    def closeEvent(self, event):
        """窗口关闭时确保线程正确退出"""
        if self.bucket_size_thread and self.bucket_size_thread.isRunning():
            self.bucket_size_thread.quit()
            self.bucket_size_thread.wait()
        event.accept()

def main():
    app = QApplication(sys.argv)
    window = R2UploaderGUI()
    window.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main() 