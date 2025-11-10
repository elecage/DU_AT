#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from hailo_sdk_client import ClientRunner

# -------------------------------------------------
# 경로/설정
# -------------------------------------------------
MODEL_NAME   = "fire"

BASE_DIR     = "/home/dlgyals/Downloads/hailo/Hailo8" #경로 수정 필요
MODEL_DIR    = os.path.join(BASE_DIR, "model")

# optimize_no_postprocess.py 가 만든 양자화된 HAR
QUANT_HAR_PATH   = os.path.join(MODEL_DIR, f"{MODEL_NAME}_quantized_model.har")

# 산출물 경로
FINAL_HAR_PATH   = os.path.join(MODEL_DIR, f"{MODEL_NAME}_final.har")
HEF_OUT_PATH     = os.path.join(MODEL_DIR, f"{MODEL_NAME}.hef")

# (옵션) 컴파일러 힌트. SDK마다 없을 수도 있음 → 실패해도 무시
COMPILER_TUNING_CMD = "performance_param(compiler_optimization_level=max)"


def main():
    print("[*] compile.py 시작")
    print("    QUANT_HAR_PATH :", QUANT_HAR_PATH)
    print("    HEF_OUT_PATH   :", HEF_OUT_PATH)

    # 0) 안전 체크
    if not os.path.isfile(QUANT_HAR_PATH):
        print(f"[!] 양자화된 HAR 파일이 없음: {QUANT_HAR_PATH}")
        sys.exit(1)

    # 1) quantized HAR 로드 (hailo8 타깃)
    runner = ClientRunner(
        har=QUANT_HAR_PATH,
        hw_arch="hailo8"
    )

    # 2) (선택) 컴파일러 최적화 옵션 넣어보기
    try:
        runner.load_model_script(COMPILER_TUNING_CMD)
        print(f"[+] 컴파일 성능 옵션 적용 성공: {COMPILER_TUNING_CMD}")
    except Exception as e:
        print(f"[i] 컴파일 성능 옵션 적용 실패 (무시하고 진행): {e}")

    # 3) 컴파일 실행
    print("[*] runner.compile() 호출 중 ...")
    hef_binary = runner.compile()
    print("[*] compile 완료")

    # 4) HEF 저장
    #    - 만약 compile()이 bytes/bytearray를 리턴했다면 그대로 저장
    #    - 혹시 None을 리턴했다면 (드물지만) save_hef()를 시도
    if isinstance(hef_binary, (bytes, bytearray)):
        with open(HEF_OUT_PATH, "wb") as f:
            f.write(hef_binary)
        print("[✓] HEF 저장 완료 (compile() 리턴 사용):", HEF_OUT_PATH)
    else:
        print("[i] compile()이 직접 바이트를 안 돌려줌. save_hef() 시도.")
        saved = False
        # 일부 SDK는 save_hef()만 제공
        try:
            runner.save_hef(HEF_OUT_PATH)
            print("[✓] HEF 저장 완료 (runner.save_hef):", HEF_OUT_PATH)
            saved = True
        except Exception as e:
            print("[i] runner.save_hef 실패:", e)

        if not saved:
            print("[!] HEF를 저장할 API를 찾지 못했습니다. SDK 버전 확인 필요.")
            # 여기서 끝내긴 하지만, 이 케이스는 보통 안 뜬다.
            # 대부분은 위에서 bytes 받아서 저장되거나, save_hef가 동작함.

    # 5) 최종 HAR 백업 (선택)
    try:
        runner.save_har(FINAL_HAR_PATH)
        print("[✓] 최종 HAR 저장 완료:", FINAL_HAR_PATH)
    except Exception as e:
        print("[i] 최종 HAR 저장 중 예외(치명적 아님):", e)

    # 6) 디버그: 출력 레이어 정보
    try:
        hn_dict = runner.get_hn_dict()
        layers_dict = hn_dict.get('layers', {})
        print("\n[디버그] 출력 후보 레이어들:")
        for lname, linfo in layers_dict.items():
            if "output_shapes" in linfo:
                shapes = linfo["output_shapes"]
                # YOLO 헤드 / output_layer 쪽만 골라서 출력
                if (
                    "conv4" in lname or
                    "conv5" in lname or
                    "conv6" in lname or
                    "yolo" in lname.lower() or
                    "output" in lname.lower()
                ):
                    print(f"   {lname} -> {shapes}")
        print("\n[*] compile.py 끝")
    except Exception as e:
        print("[i] hn_dict 디버그 중 예외(무시):", e)


if __name__ == "__main__":
    main()
