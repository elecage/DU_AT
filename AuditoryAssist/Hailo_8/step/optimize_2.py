#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import random
import sys
import numpy as np
from PIL import Image
from hailo_sdk_client import ClientRunner

MODEL_NAME = "fire"

BASE_DIR   = "/home/dlgyals/Downloads/hailo/Hailo8" #경로 수정 필요
MODEL_DIR  = os.path.join(BASE_DIR, "model")

PARSED_HAR_PATH = os.path.join(MODEL_DIR, f"{MODEL_NAME}.har")
QUANT_HAR_PATH  = os.path.join(MODEL_DIR, f"{MODEL_NAME}_quantized_model.har")

IMAGES_PATH      = "/home/dlgyals/Downloads/ultralytics/ultralytics/datasets/images5/train"
INPUT_H, INPUT_W = 640, 640
CALIB_SAMPLES    = 128
RANDOM_SEED      = 42
SAVE_CALIB_NPY   = False  # True면 calib_set.npy 덤프


def _resample():
    return getattr(Image, "Resampling", Image).BILINEAR

def resize_and_center_crop(img, out_h, out_w):
    if img.mode != 'RGB':
        img = img.convert('RGB')

    w, h = img.size
    if h < w:
        scale = out_h / h
        new_h, new_w = out_h, int(round(w * scale))
    else:
        scale = out_w / w
        new_w, new_h = out_w, int(round(h * scale))

    img = img.resize((new_w, new_h), _resample())

    left   = max(0, (new_w - out_w) // 2)
    top    = max(0, (new_h - out_h) // 2)
    right  = left + out_w
    bottom = top  + out_h
    img    = img.crop((left, top, right, bottom))

    if img.size != (out_w, out_h):
        canvas = Image.new('RGB', (out_w, out_h), (0, 0, 0))
        canvas.paste(
            img,
            ((out_w - img.size[0]) // 2,
             (out_h - img.size[1]) // 2)
        )
        img = canvas

    return img

def build_calib_dataset(images_path, out_h, out_w, k=128, seed=42, save_npy=False):
    exts = ('.jpg', '.jpeg', '.png', '.bmp', '.webp')

    all_files = [
        f for f in os.listdir(images_path)
        if os.path.splitext(f)[1].lower() in exts
    ]
    if not all_files:
        print(f"[!] {images_path} 안에 캘리브용 이미지가 없음")
        sys.exit(1)

    random.seed(seed)
    picked = random.sample(all_files, k=min(k, len(all_files)))
    print(f"[*] 캘리브 이미지 {len(picked)}장 사용 (총 {len(all_files)}장 중)")

    calib = np.empty((len(picked), out_h, out_w, 3), dtype=np.uint8)
    bad = 0

    for i, name in enumerate(sorted(picked)):
        path = os.path.join(images_path, name)
        try:
            img = Image.open(path)
            img = resize_and_center_crop(img, out_h, out_w)  # RGB
            calib[i] = np.asarray(img, dtype=np.uint8)
        except Exception as e:
            print(f"[경고] {path} 로드 실패: {e}")
            calib[i] = 0
            bad += 1

    if bad:
        print(f"[*] 로드 실패 {bad}장 → 0으로 채움")

    if save_npy:
        np.save("calib_set.npy", calib)
        print("[*] calib_set.npy 저장 완료")

    return calib


def main():
    print("[*] optimize_no_postprocess.py 시작")
    print("    PARSED_HAR_PATH :", PARSED_HAR_PATH)
    print("    QUANT_HAR_PATH  :", QUANT_HAR_PATH)
    print("    IMAGES_PATH     :", IMAGES_PATH)

    if not os.path.isfile(PARSED_HAR_PATH):
        print(f"[!] HAR 파일이 없음: {PARSED_HAR_PATH}")
        sys.exit(1)
    if not os.path.isdir(IMAGES_PATH):
        print(f"[!] 캘리브 이미지 폴더 없음: {IMAGES_PATH}")
        sys.exit(1)

    calib_np = build_calib_dataset(
        IMAGES_PATH,
        INPUT_H,
        INPUT_W,
        k=CALIB_SAMPLES,
        seed=RANDOM_SEED,
        save_npy=SAVE_CALIB_NPY
    )
    print("[*] calib_np shape:", calib_np.shape, calib_np.dtype)

    runner = ClientRunner(
        har=PARSED_HAR_PATH,
        hw_arch='hailo8'
    )


    script_cmd = "network_group_param(frames_per_infer=1)"

    try:
        # WARNING: 이건 내부 속성이라 SDK 버전에 따라 이름이 다를 수 있음
        print("DBG network_group_params:", runner._sdk_backend._network_group_params)
    except Exception as e:
        print("DBG couldn't inspect network_group_params:", e)

    try:
        runner.load_model_script(script_cmd)
        print("[*] 모델 스크립트 주입 성공:", script_cmd)
    except Exception as e:
        print("[!!] network_group_param(frames_per_infer=1) 주입 실패:", e)
        print("     -> SDK에서 이 키워드를 다르게 부를 수도 있음 (예: infer_batch_size 등).")
        print("     -> 만약 여기서 계속 실패하면, SDK 버전별 model script 레퍼런스에서")
        print("        network_group_param / frames_per_infer / batch_size / infer_batch_size")
        print("        중 실제 필드명이 뭔지 확인해서 맞춰줘야 해.")
        # 여기서 계속 진행하면 또 640 프레임 infer로 고정될 가능성 있음.

    # 양자화 / 최적화
    runner.optimize(calib_np)
    print("[*] optimize 완료")

    runner.save_har(QUANT_HAR_PATH)
    print("[✓] quantized HAR 저장 완료:", QUANT_HAR_PATH)

    # 디버그: shape 찍어보기
    try:
        hn_dict = runner.get_hn_dict()
        layers_dict = hn_dict.get('layers', {})

        print("\n[디버그] 입력 레이어 shape들:")
        for layer_name, layer_info in layers_dict.items():
            if 'input_shapes' in layer_info:
                print("   ", layer_name, "->", layer_info.get('input_shapes'))

        print("\n[디버그] 출력 레이어 shape들:")
        for layer_name, layer_info in layers_dict.items():
            if 'output_shapes' in layer_info:
                if ("conv41" in layer_name
                    or "conv42" in layer_name
                    or "conv52" in layer_name
                    or "conv53" in layer_name
                    or "conv62" in layer_name
                    or "conv63" in layer_name
                    or "output_layer" in layer_name
                    or "yolo" in layer_name.lower()):
                    print("   ", layer_name, "->", layer_info.get('output_shapes'))
    except Exception as e:
        print("[경고] hn_dict 디버그 중 예외:", e)

    print("\n[✓] optimize_no_postprocess 단계 끝")


if __name__ == "__main__":
    main()
