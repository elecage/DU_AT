from ultralytics import YOLO
import cv2
import math
import numpy as np
import logging

logging.getLogger("ultralytics").setLevel(logging.WARNING)

#image 
#img_path = cv2.imread("../streetview.png")
#img = cv2.resize(img_path, (640,640))

# start webcam
cap = cv2.VideoCapture(0)

# model
model_seg = YOLO("./AI_models/streetn.pt")
model_pot = YOLO("./AI_models/pothole.pt")
model_crack = YOLO("./AI_models/crackn.pt")
model_obs = YOLO("./AI_models/yolov8n.pt")


classNames = ["Crosswalk", "Curb-Down", "Curb-Up", "Floor", "Ground", "Hole", "Road", "Sidewalk", "Stairs-Down", "Stairs-Up"]
filter_Class = ["Curb-Down", "Curb-Up", "Hole", "Stairs-Down", "Stairs-Up"]

classNames_pot = ["pothole"]
classNames_crack = ["crack"]
classNames_obs = ["person", "bicycle", "car", "motorbike", "aeroplane", "bus", "train", "truck", "boat",
              "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
              "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
              "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite", "baseball bat",
              "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
              "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange", "broccoli",
              "carrot", "hot dog", "pizza", "donut", "cake", "chair", "sofa", "pottedplant", "bed",
              "diningtable", "toilet", "tvmonitor", "laptop", "mouse", "remote", "keyboard", "cell phone",
              "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors",
              "teddy bear", "hair drier", "toothbrush"
              ]
#obs_filter = ["person", "bicycle", "car", "motorbike"]


while True:
    success, img = cap.read()
    if not success:
        print("웹캠을 읽을 수 없습니다. cv2.VideoCapture(1)로 변경해보세요.")
        break
    
    # 웹캠을 거꾸로 설치시 사용, 아니면 주석 풀기:
    img = cv2.flip(img, 0)
    
    results_seg = model_seg(img, stream=True)
    results_pot = model_pot(img, stream=True)
    results_crack = model_crack(img, stream=True)
    results_obs = model_obs(img, stream=True)

    for r in results_seg:
        masks = r.masks  # 세그멘테이션 마스크
        
        if masks is not None:
            for i, mask in enumerate(masks):      
                cls = int(r.boxes.cls[i])
                class_name = classNames[cls]
                if class_name in filter_Class:
                    # 마스크를 바이너리 형태로 변환
                    mask_data = mask.data.cpu().numpy().astype(np.uint8).squeeze()
                    # 마스크를 이미지에 오버레이
                    color = (0, 0, 255)  # 세그멘테이션 마스크의 색상
                    mask_color = np.zeros_like(img)
                    mask_color[:, :, 2] = mask_data * 255 
                    img = cv2.addWeighted(img, 1, mask_color, 0.3, 0) 
                     
                    """ 
                    # 객체 정보
                    bbox = r.boxes.xyxy[i]
                    x1, y1, x2, y2 = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
                    org = (x1, y1)
                    font = cv2.FONT_HERSHEY_SIMPLEX
                    fontScale = 1
                    text_color = (255, 0, 0)
                    thickness = 2
                    cv2.putText(img, class_name, org, font, fontScale, text_color, thickness)
                    """

    #포트홀 탐지
    for r in results_pot:
        boxes = r.boxes
        
        if boxes is not None:
            for i, box in enumerate(boxes):
                # bounding box
                x1, y1, x2, y2 = box.xyxy[0]
                x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2) # convert to int values
                # put box in cam
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 1)
                
                """
                # class name
                cls = int(box.cls[0])
                
                # object details
                org = [x1, y1]
                font = cv2.FONT_HERSHEY_SIMPLEX
                fontScale = 1
                color = (255, 0, 0)
                thickness = 1

                cv2.putText(img, classNames_pot[cls], org, font, fontScale, color, thickness)
                """
                
    # 균열 감지
    for r in results_crack:
        masks = r.masks  # 세그멘테이션 마스크
        
        if masks is not None:
            for i, mask in enumerate(masks):      
                cls = int(r.boxes.cls[i])
                class_name = classNames[cls]

                # 마스크를 바이너리 형태로 변환
                mask_data = mask.data.cpu().numpy().astype(np.uint8).squeeze()
                # 마스크를 이미지에 오버레이
                color = (0, 0, 255)  # 세그멘테이션 마스크의 색상
                mask_color = np.zeros_like(img)
                mask_color[:, :, 2] = mask_data * 255 
                img = cv2.addWeighted(img, 1, mask_color, 0.3, 0)
                 
                """
                # 객체 정보
                bbox = r.boxes.xyxy[i]
                x1, y1, x2, y2 = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
                org = (x1, y1)
                font = cv2.FONT_HERSHEY_SIMPLEX
                fontScale = 1
                text_color = (255, 0, 0)
                thickness = 2
                cv2.putText(img, classNames_crack[cls], org, font, fontScale, text_color, thickness)
                """
                
    for r in results_obs:
        boxes = r.boxes
        
        if boxes is not None:
            for i, box in enumerate(boxes):
                cls = int(box.cls[0])
                
                if cls < 4:
                    # bounding box
                    x1, y1, x2, y2 = box.xyxy[0]
                    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2) # convert to int values
                    # put box in cam
                    cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 1)
                    
                    """
                    # object details
                    org = [x1, y1]
                    font = cv2.FONT_HERSHEY_SIMPLEX
                    fontScale = 1
                    color = (255, 0, 0)
                    thickness = 1
                    cv2.putText(img, classNames_obs[cls], org, font, fontScale, color, thickness)
                    """
                
    cv2.imshow('Webcam', img)
    if cv2.waitKey(1) == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()


                               