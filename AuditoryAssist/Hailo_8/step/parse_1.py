#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
from hailo_sdk_client import ClientRunner

MODEL_NAME = "fire"

BASE_DIR   = "/home/dlgyals/Downloads/hailo/Hailo8/model" #경로 수정 필요

onnx_path  = os.path.join(BASE_DIR, f"{MODEL_NAME}.onnx")
har_path   = os.path.join(BASE_DIR, f"{MODEL_NAME}.har")


runner = ClientRunner(hw_arch='hailo8')


end_nodes = [

    '/model.22/cv2.0/cv2.0.2/Conv', '/model.22/cv3.0/cv3.0.2/Conv',  # stride 8  (bbox, cls 등)
    '/model.22/cv2.1/cv2.1.2/Conv', '/model.22/cv3.1/cv3.1.2/Conv',  # stride 16
    '/model.22/cv2.2/cv2.2.2/Conv', '/model.22/cv3.2/cv3.2.2/Conv',  # stride 32
]

print("[*] translate_onnx_model 호출")
print("    onnx_path =", onnx_path)
hn, npz = runner.translate_onnx_model(
    onnx_path,
    MODEL_NAME,
    start_node_names=['images'],              # ONNX 입력 노드 이름
    end_node_names=end_nodes,                 # ONNX 출력 노드(헤드)
    net_input_shapes={'images': [1, 3, 640, 640]}  # NCHW batch=1
)

runner.save_har(har_path)
print("[OK] HAR saved:", har_path)


hn_dict = runner.get_hn_dict()
layers_dict = hn_dict.get('layers', {})

print("\n[디버그] 입력 레이어 정보:")
for layer_name, layer_info in layers_dict.items():
    if 'input_shapes' in layer_info:
        print("  layer:", layer_name)
        print("    input_shapes:", layer_info.get('input_shapes'))

print("\n[디버그] 출력 레이어 정보:")
for layer_name, layer_info in layers_dict.items():
    if 'output_shapes' in layer_info:
        if any(h in layer_name for h in ['cv2', 'cv3', 'Conv']):
            print("  layer:", layer_name)
            print("    output_shapes:", layer_info.get('output_shapes'))

print("\n[✓] parse 단계 완료")

